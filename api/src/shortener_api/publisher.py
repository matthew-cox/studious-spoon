"""Non-blocking click publishing (spec §5.2): redirects enqueue; a background task sends."""

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from shortener_api.telemetry import ApiTelemetry
from shortener_events import ClickEvent, SqsMessage, encode, to_sqs_attributes

logger = logging.getLogger(__name__)
BATCH_SIZE = 10  # SQS SendMessageBatch limit


class ClickPublisher(Protocol):
    def publish(self, event: ClickEvent) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class InMemoryClickPublisher:
    def __init__(self) -> None:
        self.events: list[ClickEvent] = []

    def publish(self, event: ClickEvent) -> None:
        self.events.append(event)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class BatchSender(Protocol):
    async def send(self, messages: Sequence[SqsMessage]) -> list[int]:
        """Send up to 10 messages; return indexes that failed. Raise if the call itself failed."""
        ...


class SqsBatchSender:
    def __init__(self, client: Any, queue_name: str) -> None:
        self._client = client
        self._queue_name = queue_name
        self._queue_url: str | None = None

    async def _url(self) -> str:
        if self._queue_url is None:
            response = await asyncio.to_thread(
                self._client.get_queue_url, QueueName=self._queue_name
            )
            self._queue_url = str(response["QueueUrl"])
        return self._queue_url

    async def send(self, messages: Sequence[SqsMessage]) -> list[int]:
        entries = [
            {
                "Id": str(index),
                "MessageBody": message.body,
                "MessageAttributes": to_sqs_attributes(message.attributes),
            }
            for index, message in enumerate(messages)
        ]
        response = await asyncio.to_thread(
            self._client.send_message_batch, QueueUrl=await self._url(), Entries=entries
        )
        return sorted(int(failure["Id"]) for failure in response.get("Failed", []))


class BufferedClickPublisher:
    def __init__(
        self,
        sender: BatchSender,
        telemetry: ApiTelemetry,
        *,
        maxsize: int = 10_000,
        flush_interval: float = 0.25,
        max_attempts: int = 3,
        drain_timeout: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._sender = sender
        self._telemetry = telemetry
        self._queue: asyncio.Queue[ClickEvent] = asyncio.Queue(maxsize=maxsize)
        self._flush_interval = flush_interval
        self._max_attempts = max_attempts
        self._drain_timeout = drain_timeout
        self._sleep = sleep
        self._jitter = jitter
        self._task: asyncio.Task[None] | None = None
        self._in_flight = 0  # events taken off the queue and still unresolved (for shutdown)
        telemetry.observe_buffer_size(self._queue.qsize)

    def publish(self, event: ClickEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._telemetry.click_events_dropped.add(1, {"reason": "buffer_full"})

    def _take_batch(self) -> list[ClickEvent]:
        batch: list[ClickEvent] = []
        while len(batch) < BATCH_SIZE:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    def _backoff(self, attempt: int) -> float:
        return 0.1 * 2.0 ** (attempt - 1) * (0.5 + self._jitter())

    async def _send_with_retries(self, batch: list[ClickEvent]) -> None:
        pending = batch
        for attempt in range(1, self._max_attempts + 1):
            self._in_flight = len(pending)
            try:
                failed = await self._sender.send([encode(event) for event in pending])
            except Exception:  # any transport failure; the redirect path must never see it
                logger.warning("click batch send failed (attempt %d)", attempt, exc_info=True)
                failed = list(range(len(pending)))
            self._telemetry.click_events_published.add(len(pending) - len(failed))
            pending = [pending[index] for index in failed]
            self._in_flight = len(pending)  # published entries are resolved
            if not pending:
                return
            if attempt < self._max_attempts:
                await self._sleep(self._backoff(attempt))
        self._in_flight = 0
        self._telemetry.click_events_dropped.add(len(pending), {"reason": "publish_failed"})

    async def flush_once(self) -> int:
        batch = self._take_batch()
        if batch:
            self._in_flight = len(batch)
            await self._send_with_retries(batch)  # if cancelled here, _in_flight stays counted
        return len(batch)

    async def run(self) -> None:
        while True:
            try:
                if await self.flush_once() < BATCH_SIZE:
                    await self._sleep(self._flush_interval)
            except Exception:  # keep publishing; clicks must not stop until restart
                logger.exception("click publisher loop error")
                if self._in_flight:  # the batch it was holding is gone
                    self._telemetry.click_events_dropped.add(
                        self._in_flight, {"reason": "publish_failed"}
                    )
                    self._in_flight = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="click-publisher")

    async def stop(self) -> None:
        lost = 0
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            lost = self._in_flight  # the batch the cancelled send still held
            self._in_flight = 0
        try:
            async with asyncio.timeout(self._drain_timeout):
                while await self.flush_once():
                    pass
        except TimeoutError:
            pass
        except Exception:  # shutdown must still finish its accounting
            logger.exception("click publisher drain failed")
        if total := lost + self._in_flight + self._queue.qsize():
            self._telemetry.click_events_dropped.add(total, {"reason": "shutdown"})
