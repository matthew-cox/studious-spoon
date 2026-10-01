from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from shortener_events import ClickEvent, encode, to_sqs_attributes
from shortener_processor.batch import BatchProcessor
from shortener_processor.consumer import Consumer
from shortener_processor.link_resolver import PostgresLinkResolver
from shortener_processor.rollup_store import PostgresRollupStore
from shortener_processor.sqs import SqsQueueClient
from shortener_processor.telemetry import ProcessorTelemetry

pytestmark = pytest.mark.integration


def url(sqs, name="click-events"):
    return sqs.get_queue_url(QueueName=name)["QueueUrl"]


def send(sqs, body, attributes):
    sqs.send_message(
        QueueUrl=url(sqs), MessageBody=body, MessageAttributes=to_sqs_attributes(attributes)
    )


def click(code, link_id=None, n=0):
    return encode(
        ClickEvent(
            event_id=f"e-{code}-{n}",
            occurred_at=datetime.now(UTC),
            source="api",
            code=code,
            link_id=link_id,
            referrer="https://ref.example/x",
        )
    )


async def test_receive_and_delete_round_trip(sqs_client):
    message = click("aaaaaaa")
    send(sqs_client, message.body, message.attributes)
    queue = SqsQueueClient(sqs_client, "click-events")
    [received] = await queue.receive(10, 2)
    assert received.body == message.body
    assert received.attributes["type"] == "link.clicked"
    assert await queue.depth("click-events") == 0  # in flight, not visible
    assert await queue.delete([received.receipt_handle]) == []
    assert await queue.receive(10, 0) == []


async def test_bogus_receipt_handle_is_reported_as_failed(sqs_client):
    assert await SqsQueueClient(sqs_client, "click-events").delete(["not-a-handle"]) == [
        "not-a-handle"
    ]


async def test_poison_message_is_redriven_to_the_dlq(sqs_client):
    send(sqs_client, "{not json", {"type": "link.clicked", "version": "1"})
    queue = SqsQueueClient(sqs_client, "click-events")
    receives = 0
    for _ in range(8):  # maxReceiveCount=5: it must be gone by the 6th or 7th attempt
        got = await queue.receive(10, 1)
        if not got:
            break
        receives += 1
        sqs_client.change_message_visibility(
            QueueUrl=url(sqs_client), ReceiptHandle=got[0].receipt_handle, VisibilityTimeout=0
        )
    assert receives == 5
    assert await queue.depth("click-events-dlq") == 1


async def test_end_to_end_batch_against_real_queue_and_db(
    sqs_client, migrated, insert_link, meter, metric_value
):
    link = insert_link(code="e2e0001")
    for n in range(3):
        m = click("e2e0001", n=n)  # no link_id: exercises the resolver
        send(sqs_client, m.body, m.attributes)
    m = click("e2e0001", link_id=link, n=9)
    send(sqs_client, m.body, m.attributes)
    send(sqs_client, "{not json", {"type": "link.clicked", "version": "1"})

    engine = create_async_engine(migrated.url("processor_user"))
    queue = SqsQueueClient(sqs_client, "click-events")
    processor = BatchProcessor(
        queue, PostgresLinkResolver(engine), PostgresRollupStore(engine), ProcessorTelemetry(meter)
    )
    try:
        outcome = await Consumer(queue, processor, wait_seconds=2, window_seconds=0.5).run_once()
    finally:
        await engine.dispose()

    assert outcome is not None
    assert (outcome.ok, outcome.invalid) == (4, 1)
    with migrated.connect("migrator") as conn:
        assert conn.execute("SELECT sum(count) FROM analytics.link_clicks_hourly").fetchone() == (
            4,
        )
        assert conn.execute(
            "SELECT referrer_host, count FROM analytics.link_referrers_daily"
        ).fetchall() == [("ref.example", 4)]
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 4
    assert metric_value("shortener.processor.messages", {"result": "invalid"}) == 1
