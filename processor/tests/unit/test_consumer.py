import asyncio

import pytest

from shortener_processor.batch import BatchOutcome
from shortener_processor.consumer import Consumer
from shortener_processor.queue import ReceivedMessage


def msgs(start: int, count: int) -> list[ReceivedMessage]:
    return [ReceivedMessage(f"m{i}", f"r{i}", "{}") for i in range(start, start + count)]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedQueue:
    """Each receive returns the next scripted list and advances the fake clock by `step`."""

    def __init__(self, script, clock: FakeClock, step: float = 0.3) -> None:
        self.script = list(script)
        self.calls: list[tuple[int, int]] = []
        self._clock = clock
        self._step = step

    async def receive(self, max_messages, wait_seconds):
        self.calls.append((max_messages, wait_seconds))
        self._clock.now += self._step
        await asyncio.sleep(0)
        return self.script.pop(0) if self.script else []

    async def delete(self, receipt_handles):
        return []

    async def depth(self, queue_name):
        return 0


class RecordingProcessor:
    def __init__(self, error: Exception | None = None) -> None:
        self.batches: list[list[ReceivedMessage]] = []
        self.error = error

    async def process(self, messages):
        self.batches.append(list(messages))
        if self.error is not None:
            error, self.error = self.error, None  # fail once
            raise error
        return BatchOutcome(len(messages), 0, 0, len(messages), 0)


async def test_empty_first_poll_returns_nothing_after_a_long_poll():
    clock = FakeClock()
    queue = ScriptedQueue([[]], clock)
    consumer = Consumer(queue, RecordingProcessor(), monotonic=clock)
    assert await consumer.collect() == []
    assert queue.calls == [(10, 20)]


async def test_collects_until_the_window_closes():
    clock = FakeClock()
    script = [msgs(n * 10, 10) for n in range(8)]
    queue = ScriptedQueue(script, clock)
    batch = await Consumer(queue, RecordingProcessor(), monotonic=clock).collect()
    # Each receive advances the clock 0.3 s. The first batch arrives at t=0.3, so the window
    # closes at 1.3. The window is checked before each receive: receives start at
    # 0, 0.3, 0.6, 0.9 and 1.2 (all < 1.3), so five receives = 50 messages, then stop at t=1.5.
    assert len(batch) == 50
    assert len(queue.calls) == 5
    assert queue.calls[0] == (10, 20)
    assert all(wait == 1 for _, wait in queue.calls[1:])


async def test_stops_at_max_messages_and_asks_only_for_what_fits():
    clock = FakeClock()
    queue = ScriptedQueue([msgs(0, 10), msgs(10, 10), msgs(20, 5)], clock, step=0.0)
    batch = await Consumer(queue, RecordingProcessor(), max_messages=25, monotonic=clock).collect()
    assert len(batch) == 25
    assert [n for n, _ in queue.calls] == [10, 10, 5]


async def test_run_once_hands_the_batch_to_the_processor():
    clock = FakeClock()
    processor = RecordingProcessor()
    consumer = Consumer(ScriptedQueue([msgs(0, 3)], clock), processor, monotonic=clock)
    outcome = await consumer.run_once()
    assert outcome is not None and outcome.ok == 3
    assert len(processor.batches[0]) == 3


async def test_run_survives_a_failing_batch():
    clock = FakeClock()
    slept: list[float] = []
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    processor = RecordingProcessor(error=ConnectionError("db down"))
    # step=2.0 > window: each batch closes after one follow-up receive, so the two scripted
    # messages land in two separate batches (an empty script returns []).
    queue = ScriptedQueue([msgs(0, 1), [], msgs(1, 1)], clock, step=2.0)
    consumer = Consumer(queue, processor, monotonic=clock, sleep=sleep, error_backoff_seconds=1.0)

    async def stop_when_second_batch_processed() -> None:
        while len(processor.batches) < 2:
            await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(consumer.run(stop), stop_when_second_batch_processed())
    assert len(processor.batches) == 2  # the loop kept going after the failure
    assert slept == [1.0]


async def test_heartbeat_tracks_the_last_receive():
    clock = FakeClock()
    consumer = Consumer(ScriptedQueue([[]], clock), RecordingProcessor(), monotonic=clock)
    await consumer.collect()
    clock.now += 5
    assert consumer.seconds_since_heartbeat() == pytest.approx(5.0)


class FlakyFollowUpQueue(ScriptedQueue):
    """First receive returns messages; every later receive raises."""

    async def receive(self, max_messages, wait_seconds):
        if self.calls:
            self.calls.append((max_messages, wait_seconds))
            raise ConnectionError("sqs blip")
        return await super().receive(max_messages, wait_seconds)


async def test_failing_follow_up_receive_still_processes_the_messages_in_hand():
    clock = FakeClock()
    processor = RecordingProcessor()
    queue = FlakyFollowUpQueue([msgs(0, 10)], clock)
    outcome = await Consumer(queue, processor, monotonic=clock).run_once()
    assert outcome is not None and outcome.ok == 10
    assert len(processor.batches[0]) == 10


async def test_failing_first_receive_propagates():
    class Broken(ScriptedQueue):
        async def receive(self, max_messages, wait_seconds):
            raise ConnectionError("sqs down")

    clock = FakeClock()
    with pytest.raises(ConnectionError):
        await Consumer(Broken([], clock), RecordingProcessor(), monotonic=clock).collect()


async def test_heartbeat_stays_fresh_while_every_receive_fails():
    clock = FakeClock()
    stop = asyncio.Event()
    ages: list[float] = []

    class AlwaysFailing(ScriptedQueue):
        async def receive(self, max_messages, wait_seconds):
            ages.append(consumer.seconds_since_heartbeat())
            raise ConnectionError("sqs outage")

    async def sleep(seconds: float) -> None:
        clock.now += 30.0  # an outage far longer than any staleness limit
        if len(ages) == 3:
            stop.set()

    consumer = Consumer(
        AlwaysFailing([], clock), RecordingProcessor(), monotonic=clock, sleep=sleep
    )
    await consumer.run(stop)
    assert ages == [0.0, 0.0, 0.0]
