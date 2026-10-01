from datetime import UTC, datetime
from uuid import UUID

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from shortener_events import ClickEvent, encode
from shortener_processor.batch import BatchProcessor
from shortener_processor.queue import ReceivedMessage
from shortener_processor.rollup_store import CommitResult
from shortener_processor.telemetry import ProcessorTelemetry

LINK = UUID("00000000-0000-0000-0000-00000000000a")
PRODUCER_A = ("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7")
PRODUCER_B = ("0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331")


def message(n: int, traceparent: str | None) -> ReceivedMessage:
    event = ClickEvent(event_id=f"e{n}", occurred_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
                       source="api", code="aaaaaaa", link_id=LINK)  # fmt: skip
    encoded = encode(event, traceparent=traceparent)
    return ReceivedMessage(f"m{n}", f"r{n}", encoded.body, encoded.attributes)


def tp(ids: tuple[str, str]) -> str:
    return f"00-{ids[0]}-{ids[1]}-01"


class Queue:
    async def receive(self, max_messages, wait_seconds):
        return []

    async def delete(self, receipt_handles):
        return []

    async def depth(self, queue_name):
        return 0


class Resolver:
    async def resolve(self, codes):
        return {}


class Store:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def commit(self, deltas, now):
        if self.error:
            raise self.error
        return CommitResult(deltas.link_ids, frozenset())


@pytest.fixture
def spans():
    return InMemorySpanExporter()


@pytest.fixture
def make(spans, meter):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))

    def _make(store=None):
        return BatchProcessor(Queue(), Resolver(), store or Store(), ProcessorTelemetry(meter),
                              tracer=provider.get_tracer("t"))  # fmt: skip

    return _make


async def test_batch_span_links_to_each_producer(make, spans):
    await make().process([message(1, tp(PRODUCER_A)), message(2, tp(PRODUCER_B)), message(3, None)])
    [span] = spans.get_finished_spans()
    assert span.name == "process click batch"
    linked = {(f"{x.context.trace_id:032x}", f"{x.context.span_id:016x}") for x in span.links}
    assert linked == {PRODUCER_A, PRODUCER_B}
    assert span.parent is None  # links, not a parent: a batch has many producers
    assert span.attributes["messaging.batch.message_count"] == 3
    assert span.attributes["messaging.system"] == "aws_sqs"


async def test_malformed_traceparent_is_ignored(make, spans):
    outcome = await make().process([message(1, "garbage"), message(2, "00-zz-yy-01")])
    assert outcome.ok == 2
    [span] = spans.get_finished_spans()
    assert span.links == ()


async def test_commit_failure_is_recorded_on_the_span(make, spans):
    with pytest.raises(ConnectionError):
        await make(Store(error=ConnectionError("db down"))).process([message(1, tp(PRODUCER_A))])
    [span] = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert any(e.name == "exception" for e in span.events)
