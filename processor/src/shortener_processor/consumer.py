"""Collect up to N messages within a short window, then process them (spec §5.3 step 1)."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from shortener_processor.batch import BatchOutcome
from shortener_processor.queue import QueueClient, ReceivedMessage

logger = logging.getLogger(__name__)
SQS_MAX_PER_RECEIVE = 10


class Processor(Protocol):
    async def process(self, messages: Sequence[ReceivedMessage]) -> BatchOutcome: ...


class Consumer:
    def __init__(
        self,
        queue: QueueClient,
        processor: Processor,
        *,
        max_messages: int = 100,
        window_seconds: float = 1.0,
        wait_seconds: int = 20,
        error_backoff_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._queue = queue
        self._processor = processor
        self._max = max_messages
        self._window = window_seconds
        self._wait = wait_seconds
        self._backoff = error_backoff_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._heartbeat = monotonic()

    def seconds_since_heartbeat(self) -> float:
        return self._monotonic() - self._heartbeat

    async def collect(self) -> list[ReceivedMessage]:
        batch: list[ReceivedMessage] = []
        deadline: float | None = None
        while len(batch) < self._max:
            if deadline is not None and self._monotonic() >= deadline:
                break
            wanted = min(SQS_MAX_PER_RECEIVE, self._max - len(batch))
            received = await self._queue.receive(wanted, self._wait if not batch else 1)
            self._heartbeat = self._monotonic()
            if not batch:
                if not received:
                    return []
                deadline = self._monotonic() + self._window
            batch.extend(received)
        return batch

    async def run_once(self) -> BatchOutcome | None:
        batch = await self.collect()
        if not batch:
            return None
        return await self._processor.process(batch)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # the loop must outlive any one batch (spec §9)
                logger.exception("click batch failed; undeleted messages will be redelivered")
                await self._sleep(self._backoff)
