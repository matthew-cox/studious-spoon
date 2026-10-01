from datetime import UTC, datetime

import pytest

from shortener_api.publisher import SqsBatchSender
from shortener_events import ClickEvent, SqsMessage, decode, encode, from_sqs_attributes

pytestmark = pytest.mark.integration


async def test_batch_lands_on_the_queue_with_attributes(sqs_client):
    events = [
        ClickEvent(
            event_id=f"e{n}",
            occurred_at=datetime(2026, 10, 1, tzinfo=UTC),
            source="api",
            code=f"c{n}",
        )
        for n in range(3)
    ]
    sender = SqsBatchSender(sqs_client, "click-events")
    assert await sender.send([encode(e) for e in events]) == []
    url = sqs_client.get_queue_url(QueueName="click-events")["QueueUrl"]
    received = sqs_client.receive_message(
        QueueUrl=url, MaxNumberOfMessages=10, MessageAttributeNames=["All"], WaitTimeSeconds=2
    )["Messages"]
    decoded = sorted(
        (
            decode(SqsMessage(m["Body"], from_sqs_attributes(m["MessageAttributes"])))
            for m in received
        ),
        key=lambda e: e.event_id,
    )
    assert decoded == events


async def test_unknown_queue_raises(sqs_client):
    sender = SqsBatchSender(sqs_client, "no-such-queue")
    with pytest.raises(Exception, match=r"(?i)queue"):
        await sender.send([])
