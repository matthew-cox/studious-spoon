import asyncio
from datetime import UTC, datetime

import pytest

from shortener_api.publisher import BATCH_SIZE, BufferedClickPublisher, InMemoryClickPublisher
from shortener_api.telemetry import ApiTelemetry
from shortener_events import ClickEvent, SqsMessage, decode


def event(n: int) -> ClickEvent:
    return ClickEvent(
        event_id=f"e{n}", occurred_at=datetime(2026, 10, 1, tzinfo=UTC), source="api", code=f"c{n}"
    )


class FakeSender:
    """Scripted outcomes per call: a list of failed indexes, or an exception to raise."""

    def __init__(self, outcomes=None) -> None:
        self.calls: list[list[SqsMessage]] = []
        self._outcomes = list(outcomes or [])

    async def send(self, messages):
        self.calls.append(list(messages))
        outcome = self._outcomes.pop(0) if self._outcomes else []
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)  # yield to the loop without waiting


@pytest.fixture
def sleep():
    return RecordingSleep()


@pytest.fixture
def make_publisher(meter, sleep):
    def _make(sender, **kwargs):
        options = {"sleep": sleep, "jitter": lambda: 0.5, "flush_interval": 0.25} | kwargs
        return BufferedClickPublisher(sender, ApiTelemetry(meter), **options)

    return _make


async def test_publish_is_buffered_until_flushed(make_publisher):
    sender = FakeSender()
    publisher = make_publisher(sender)
    publisher.publish(event(1))
    assert sender.calls == []
    assert await publisher.flush_once() == 1
    [[message]] = sender.calls
    assert decode(message) == event(1)


async def test_batches_hold_at_most_ten(make_publisher, metric_value):
    sender = FakeSender()
    publisher = make_publisher(sender)
    for n in range(23):
        publisher.publish(event(n))
    while await publisher.flush_once():
        pass
    assert [len(call) for call in sender.calls] == [BATCH_SIZE, BATCH_SIZE, 3]
    assert metric_value("shortener.click_events.published") == 23


async def test_full_buffer_drops_and_counts(make_publisher, metric_value):
    publisher = make_publisher(FakeSender(), maxsize=2)
    for n in range(5):
        publisher.publish(event(n))  # never raises
    assert metric_value("shortener.click_events.dropped", {"reason": "buffer_full"}) == 3
    assert metric_value("shortener.click_events.buffer_size") == 2


async def test_only_failed_entries_are_retried(make_publisher, sleep, metric_value):
    sender = FakeSender(outcomes=[[1], []])
    publisher = make_publisher(sender)
    for n in range(3):
        publisher.publish(event(n))
    await publisher.flush_once()
    assert [decode(m).event_id for m in sender.calls[1]] == ["e1"]
    assert sleep.delays == [0.1]  # 0.1 * 2**0 * (0.5 + 0.5)
    assert metric_value("shortener.click_events.published") == 3


async def test_gives_up_after_max_attempts_and_counts_drops(make_publisher, sleep, metric_value):
    sender = FakeSender(outcomes=[ConnectionError("sqs down")] * 3)
    publisher = make_publisher(sender)
    publisher.publish(event(1))
    publisher.publish(event(2))
    await publisher.flush_once()  # must not raise
    assert len(sender.calls) == 3
    assert sleep.delays == [0.1, 0.2]
    assert metric_value("shortener.click_events.dropped", {"reason": "publish_failed"}) == 2
    assert metric_value("shortener.click_events.published") == 0


async def test_background_loop_flushes_and_stop_drains(make_publisher):
    sender = FakeSender()
    publisher = make_publisher(sender)
    await publisher.start()
    for n in range(12):
        publisher.publish(event(n))
    await publisher.stop()
    assert sum(len(call) for call in sender.calls) == 12


async def test_stop_counts_undrained_events_as_shutdown_drops(make_publisher, metric_value):
    class HangingSender:
        async def send(self, messages):
            await asyncio.Event().wait()

    publisher = make_publisher(HangingSender(), drain_timeout=0.01)
    publisher.publish(event(1))
    publisher.publish(event(2))
    await publisher.stop()
    assert metric_value("shortener.click_events.dropped", {"reason": "shutdown"}) == 2


async def test_in_memory_publisher_records_events():
    publisher = InMemoryClickPublisher()
    await publisher.start()
    publisher.publish(event(1))
    await publisher.stop()
    assert publisher.events == [event(1)]


class SignallingSender:
    """Records calls, signals each one, and hangs on calls after `ok_calls` successful ones."""

    def __init__(self, outcomes=None) -> None:
        self.called = asyncio.Event()
        self.calls: list[list[SqsMessage]] = []
        self._outcomes = list(outcomes or [])

    async def send(self, messages):
        self.calls.append(list(messages))
        self.called.set()
        if not self._outcomes:
            await asyncio.Event().wait()
        return self._outcomes.pop(0)


async def test_stop_counts_the_batch_cancelled_mid_send_exactly_once(make_publisher, metric_value):
    sender = SignallingSender()
    publisher = make_publisher(sender, drain_timeout=0.01)
    await publisher.start()
    for n in range(3):
        publisher.publish(event(n))
    await asyncio.wait_for(sender.called.wait(), 1)  # the loop now holds all three
    await publisher.stop()
    assert metric_value("shortener.click_events.dropped", {"reason": "shutdown"}) == 3


async def test_shutdown_drop_excludes_entries_already_published(make_publisher, metric_value):
    sender = SignallingSender(outcomes=[[1]])  # first send: entry 1 fails; the retry hangs
    publisher = make_publisher(sender, drain_timeout=0.01)
    await publisher.start()
    for n in range(3):
        publisher.publish(event(n))
    async with asyncio.timeout(1):
        while len(sender.calls) < 2:
            await asyncio.sleep(0)
    await publisher.stop()
    assert metric_value("shortener.click_events.published") == 2
    assert metric_value("shortener.click_events.dropped", {"reason": "shutdown"}) == 1


async def test_loop_survives_an_unexpected_error(make_publisher):
    calls = 0

    async def flaky_sleep(delay: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        await asyncio.sleep(0)

    sender = SignallingSender(outcomes=[[]])
    publisher = make_publisher(sender, sleep=flaky_sleep)
    await publisher.start()
    async with asyncio.timeout(1):
        while calls < 2:  # the loop hit the error and went round again
            await asyncio.sleep(0)
        publisher.publish(event(1))
        await sender.called.wait()
    await publisher.stop()
    assert [decode(m).event_id for m in sender.calls[0]] == ["e1"]
