from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from shortener_events import ClickEvent, encode
from shortener_processor.batch import BatchProcessor
from shortener_processor.queue import ReceivedMessage
from shortener_processor.rollup_store import CommitResult
from shortener_processor.telemetry import ProcessorTelemetry

A = UUID("00000000-0000-0000-0000-00000000000a")
B = UUID("00000000-0000-0000-0000-00000000000b")
NOW = datetime(2026, 10, 1, 12, 0, 10, tzinfo=UTC)


def message(n: int, *, code="aaaaaaa", link_id=A, referrer=None, event_id=None, seconds_ago=10):
    event = ClickEvent(
        event_id=event_id or f"e{n}",
        occurred_at=NOW - timedelta(seconds=seconds_ago),
        source="api",
        code=code,
        link_id=link_id,
        referrer=referrer,
    )
    encoded = encode(event)
    return ReceivedMessage(f"m{n}", f"r{n}", encoded.body, encoded.attributes)


def poison(n: int) -> ReceivedMessage:
    return ReceivedMessage(f"m{n}", f"r{n}", "{not json", {"type": "link.clicked", "version": "1"})


class FakeQueue:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.deleted: list[str] = []
        self._failing = failing or set()

    async def receive(self, max_messages, wait_seconds):
        return []

    async def delete(self, receipt_handles):
        self.deleted.extend(h for h in receipt_handles if h not in self._failing)
        return [h for h in receipt_handles if h in self._failing]

    async def depth(self, queue_name):
        return 0


class FakeResolver:
    def __init__(self, known: dict[str, UUID]) -> None:
        self.known = known
        self.calls: list[set[str]] = []

    async def resolve(self, codes):
        self.calls.append(set(codes))
        return {c: self.known[c] for c in codes if c in self.known}


class FakeStore:
    def __init__(self, skipped: frozenset[UUID] = frozenset(), error: Exception | None = None):
        self.commits = []
        self._skipped = skipped
        self._error = error

    async def commit(self, deltas, now):
        if self._error is not None:
            raise self._error
        self.commits.append((deltas, now))
        return CommitResult(deltas.link_ids - self._skipped, deltas.link_ids & self._skipped)


@pytest.fixture
def make(meter):
    def _make(queue=None, resolver=None, store=None):
        queue = queue or FakeQueue()
        resolver = resolver or FakeResolver({})
        store = store or FakeStore()
        processor = BatchProcessor(
            queue, resolver, store, ProcessorTelemetry(meter), clock=lambda: NOW,
            perf_counter=iter([0.0, 0.25]).__next__,
        )  # fmt: skip
        return processor, queue, resolver, store

    return _make


async def test_valid_batch_commits_then_deletes(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process(
        [message(1), message(2, link_id=B, referrer="https://n.example/x")]
    )
    [(deltas, now)] = store.commits
    assert now == NOW
    assert deltas.hourly == {
        (A, datetime(2026, 10, 1, 12, tzinfo=UTC)): 1,
        (B, datetime(2026, 10, 1, 12, tzinfo=UTC)): 1,
    }
    assert deltas.referrers[(B, date(2026, 10, 1), "n.example")] == 1
    assert deltas.referrers[(A, date(2026, 10, 1), "(direct)")] == 1
    assert queue.deleted == ["r1", "r2"]
    assert (outcome.ok, outcome.invalid, outcome.unknown_link, outcome.deleted) == (2, 0, 0, 2)
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 2
    assert metric_value("shortener.processor.event_lag") == 2
    assert metric_value("shortener.processor.batch.duration") == 1


async def test_invalid_message_is_counted_and_left_on_the_queue(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process([message(1), poison(2)])
    assert queue.deleted == ["r1"]
    assert outcome.invalid == 1
    assert len(store.commits) == 1
    assert metric_value("shortener.processor.messages", {"result": "invalid"}) == 1


async def test_missing_link_id_is_resolved_once_per_batch(make):
    resolver = FakeResolver({"aaaaaaa": A})
    processor, _, resolver, store = make(resolver=resolver)
    await processor.process([message(1, link_id=None), message(2, link_id=None), message(3)])
    assert resolver.calls == [{"aaaaaaa"}]
    assert store.commits[0][0].hourly == {(A, datetime(2026, 10, 1, 12, tzinfo=UTC)): 3}


async def test_unresolvable_code_is_unknown_link_and_deleted(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process([message(1, code="gone000", link_id=None)])
    assert store.commits == []  # nothing to commit
    assert queue.deleted == ["r1"]
    assert outcome.unknown_link == 1
    assert metric_value("shortener.processor.messages", {"result": "unknown_link"}) == 1


async def test_store_skipped_links_count_as_unknown_and_are_deleted(make, metric_value):
    processor, queue, _, _ = make(store=FakeStore(skipped=frozenset({B})))
    outcome = await processor.process([message(1), message(2, link_id=B)])
    assert (outcome.ok, outcome.unknown_link) == (1, 1)
    assert sorted(queue.deleted) == ["r1", "r2"]
    assert metric_value("shortener.processor.messages", {"result": "unknown_link"}) == 1


async def test_commit_failure_deletes_nothing(make):
    processor, queue, _, _ = make(store=FakeStore(error=ConnectionError("db down")))
    with pytest.raises(ConnectionError):
        await processor.process([message(1), message(2)])
    assert queue.deleted == []


async def test_duplicate_event_ids_count_once_and_all_copies_are_deleted(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process([message(1, event_id="dup"), message(2, event_id="dup")])
    assert store.commits[0][0].hourly == {(A, datetime(2026, 10, 1, 12, tzinfo=UTC)): 1}
    assert sorted(queue.deleted) == ["r1", "r2"]
    assert outcome.ok == 1
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 1


async def test_delete_failures_are_reported_not_raised(make):
    processor, _, _, _ = make(queue=FakeQueue(failing={"r2"}))
    outcome = await processor.process([message(1), message(2)])
    assert (outcome.deleted, outcome.delete_failures) == (1, 1)


async def test_event_lag_is_never_negative(make, metric_points):
    processor, _, _, _ = make()
    await processor.process([message(1, seconds_ago=-30)])  # producer clock ahead of ours
    [point] = metric_points("shortener.processor.event_lag")
    assert point.sum == 0


async def test_empty_batch_is_a_no_op(make):
    processor, queue, resolver, store = make()
    outcome = await processor.process([])
    assert (store.commits, queue.deleted, resolver.calls) == ([], [], [])
    assert outcome.ok == 0


async def test_control_character_code_is_invalid_and_does_not_poison_the_batch(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process([message(1), message(2, code="a\x00b"), message(3)])
    assert sorted(queue.deleted) == ["r1", "r3"]  # the NUL message stays on the queue
    assert (outcome.ok, outcome.invalid, outcome.deleted) == (2, 1, 2)
    assert store.commits[0][0].hourly == {(A, datetime(2026, 10, 1, 12, tzinfo=UTC)): 2}
    assert metric_value("shortener.processor.messages", {"result": "invalid"}) == 1


async def test_nothing_is_recorded_between_commit_and_delete(meter, metric_value):
    seen: list[float] = []

    class SnoopingQueue(FakeQueue):
        async def delete(self, receipt_handles):
            seen.append(metric_value("shortener.processor.messages"))
            return await super().delete(receipt_handles)

    processor = BatchProcessor(
        SnoopingQueue(), FakeResolver({}), FakeStore(), ProcessorTelemetry(meter),
        clock=lambda: NOW,
    )  # fmt: skip
    await processor.process([message(1)])
    assert seen == [0.0]  # metrics are recorded only after the delete returned
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 1
