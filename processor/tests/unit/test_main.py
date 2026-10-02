import asyncio
import contextlib

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from shortener_processor.batch import BatchOutcome
from shortener_processor.consumer import Consumer
from shortener_processor.main import Runtime, build_runtime, poll_queue_depth, serve
from shortener_processor.queue import ReceivedMessage
from shortener_processor.sqs import SqsQueueClient
from shortener_processor.telemetry import ProcessorTelemetry


class IdleQueue:
    """Long-poll that never yields messages; depth errors on demand."""

    def __init__(self, depth_error: bool = False) -> None:
        self.depth_calls: list[str] = []
        self._depth_error = depth_error

    async def receive(self, max_messages, wait_seconds):
        await asyncio.sleep(0.01)
        return []

    async def delete(self, receipt_handles):
        return []

    async def depth(self, queue_name):
        self.depth_calls.append(queue_name)
        if self._depth_error:
            raise ConnectionError("sqs down")
        return 3


class HangingProcessor:
    async def process(self, messages):
        await asyncio.Event().wait()
        return BatchOutcome(0, 0, 0, 0, 0)


def runtime(settings, meter, queue, consumer) -> Runtime:
    return Runtime(
        settings=settings,
        engine=create_async_engine(str(settings.database_url)),
        queue=queue,
        consumer=consumer,
        telemetry=ProcessorTelemetry(meter),
    )


async def test_build_runtime_without_network(settings):
    built = build_runtime(settings, install_globals=False)
    assert isinstance(built.queue, SqsQueueClient)
    assert isinstance(built.consumer, Consumer)
    assert built.tracer_provider is not None
    assert built.tracer_provider.resource.attributes["service.name"] == "shortener-click-processor"
    assert built.telemetry_shutdown is not None
    built.telemetry_shutdown()
    await built.engine.dispose()


def test_engine_has_connect_and_statement_timeouts(settings, monkeypatch):
    captured: dict[str, object] = {}

    def fake_create(url, **kwargs):
        captured.update(kwargs)
        return create_async_engine(url)

    monkeypatch.setattr("shortener_processor.main.create_async_engine", fake_create)
    build_runtime(settings, install_globals=False)
    assert captured["connect_args"] == {
        "connect_timeout": 5,
        "options": "-c statement_timeout=20000",
    }
    assert captured["pool_pre_ping"] is True


async def test_poll_queue_depth_records_both_queues(settings, meter, metric_value):
    telemetry, stop, queue = ProcessorTelemetry(meter), asyncio.Event(), IdleQueue()

    async def sleep(_: float) -> None:
        stop.set()

    await poll_queue_depth(
        queue, ["click-events", "click-events-dlq"], telemetry, 30, stop, sleep=sleep
    )
    assert queue.depth_calls == ["click-events", "click-events-dlq"]
    assert metric_value("shortener.queue.depth", {"queue": "click-events-dlq"}) == 3


async def test_poll_queue_depth_survives_errors(settings, meter):
    telemetry, stop = ProcessorTelemetry(meter), asyncio.Event()

    async def sleep(_: float) -> None:
        stop.set()

    await poll_queue_depth(
        IdleQueue(depth_error=True), ["click-events"], telemetry, 30, stop, sleep=sleep
    )


async def test_serve_stops_on_signal_event(settings, meter):
    queue = IdleQueue()
    consumer = Consumer(queue, HangingProcessor(), wait_seconds=0)
    stop = asyncio.Event()
    serving = asyncio.create_task(serve(runtime(settings, meter, queue, consumer), stop))
    await asyncio.sleep(0.05)  # let it start (real event loop, bounded)
    stop.set()
    await asyncio.wait_for(serving, timeout=2)


async def test_shutdown_cancels_a_batch_that_exceeds_the_grace_period(settings, meter):
    class OneMessageQueue(IdleQueue):
        async def receive(self, max_messages, wait_seconds):
            await asyncio.sleep(0)
            return [ReceivedMessage("m1", "r1", "{}")]

    queue = OneMessageQueue()
    consumer = Consumer(queue, HangingProcessor(), wait_seconds=0, window_seconds=0.001)
    stop = asyncio.Event()
    serving = asyncio.create_task(serve(runtime(settings, meter, queue, consumer), stop))
    await asyncio.sleep(0.05)
    stop.set()
    # grace is 0.2 s; the hanging batch is cancelled and serve returns well within 2 s
    await asyncio.wait_for(serving, timeout=2)


# ---- liveness / readiness / shutdown behaviour (real loop, waits bounded <= 0.05 s) ----


class FakeEngine:
    """Stands in for AsyncEngine: connect() succeeds or fails; dispose() is recorded."""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.disposed = False

    @contextlib.asynccontextmanager
    async def connect(self):
        if not self.healthy:
            raise ConnectionRefusedError("db down")
        yield self

    async def execute(self, _statement):
        return None

    async def dispose(self) -> None:
        self.disposed = True


class BlockedQueue(IdleQueue):
    """receive() never returns, so the heartbeat stays where the test left it."""

    async def receive(self, max_messages, wait_seconds):
        await asyncio.Event().wait()
        return []


class ExitedConsumer:
    """A consumer whose task finishes immediately (the loop died)."""

    async def run(self, stop):
        return None

    def seconds_since_heartbeat(self) -> float:
        return 0.0


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Serving:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.stop = asyncio.Event()
        self.server: asyncio.Server | None = None
        self.task = asyncio.create_task(serve(rt, self.stop, on_started=self._started))

    def _started(self, server: asyncio.Server) -> None:
        self.server = server

    async def base(self) -> str:
        while self.server is None:
            await asyncio.sleep(0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def finish(self) -> None:
        self.stop.set()
        await asyncio.wait_for(self.task, timeout=2)


async def status(base: str, path: str) -> int:
    async with httpx.AsyncClient() as http:
        return (await http.get(base + path)).status_code


def fake_runtime(settings, meter, queue, consumer, engine) -> Runtime:
    return Runtime(
        settings=settings,
        engine=engine,
        queue=queue,
        consumer=consumer,
        telemetry=ProcessorTelemetry(meter),
    )


async def test_healthz_is_200_while_the_consumer_is_alive_and_fresh(settings, meter):
    clock = Clock()
    consumer = Consumer(BlockedQueue(), HangingProcessor(), monotonic=clock)
    serving = Serving(fake_runtime(settings, meter, BlockedQueue(), consumer, FakeEngine()))
    assert await status(await serving.base(), "/healthz") == 200
    await serving.finish()


async def test_healthz_is_503_once_the_heartbeat_is_stale(settings, meter):
    clock = Clock()
    consumer = Consumer(BlockedQueue(), HangingProcessor(), wait_seconds=0, monotonic=clock)
    serving = Serving(fake_runtime(settings, meter, BlockedQueue(), consumer, FakeEngine()))
    base = await serving.base()
    # receive_wait_seconds defaults to 20, so the limit is max(10, 3 * 20) = 60 s
    clock.now = 59.0
    assert await status(base, "/healthz") == 200
    clock.now = 61.0
    assert await status(base, "/healthz") == 503
    await serving.finish()


async def test_stale_limit_has_a_10_second_floor(settings, meter):
    clock = Clock()
    short = settings.model_copy(update={"receive_wait_seconds": 1})
    consumer = Consumer(BlockedQueue(), HangingProcessor(), wait_seconds=0, monotonic=clock)
    serving = Serving(fake_runtime(short, meter, BlockedQueue(), consumer, FakeEngine()))
    base = await serving.base()
    clock.now = 9.0  # 3 * 1 = 3 s, but the floor is 10 s
    assert await status(base, "/healthz") == 200
    clock.now = 11.0
    assert await status(base, "/healthz") == 503
    await serving.finish()


async def test_healthz_is_503_when_the_consumer_task_has_finished(settings, meter):
    serving = Serving(fake_runtime(settings, meter, IdleQueue(), ExitedConsumer(), FakeEngine()))
    base = await serving.base()
    await asyncio.sleep(0.01)  # let the consumer task complete
    assert await status(base, "/healthz") == 503
    await serving.finish()


async def test_readyz_is_200_when_select_1_succeeds(settings, meter):
    consumer = Consumer(BlockedQueue(), HangingProcessor(), monotonic=Clock())
    serving = Serving(fake_runtime(settings, meter, BlockedQueue(), consumer, FakeEngine()))
    assert await status(await serving.base(), "/readyz") == 200
    await serving.finish()


async def test_readyz_is_503_when_the_database_is_unreachable(settings, meter):
    # unit settings point at 127.0.0.1:1, so the real engine cannot connect
    consumer = Consumer(BlockedQueue(), HangingProcessor(), monotonic=Clock())
    rt = runtime(settings, meter, BlockedQueue(), consumer)
    serving = Serving(rt)
    assert await status(await serving.base(), "/readyz") == 503
    await serving.finish()


class GatedProcessor:
    """Blocks until released; records whether it completed or was cancelled."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.completed = False
        self.cancelled = False

    async def process(self, messages):
        self.started.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.completed = True
        return BatchOutcome(len(messages), 0, 0, len(messages), 0)


class OneMessageQueue(IdleQueue):
    async def receive(self, max_messages, wait_seconds):
        await asyncio.sleep(0)
        return [ReceivedMessage("m1", "r1", "{}")]


async def test_batch_finishing_within_grace_completes_and_is_not_cancelled(settings, meter):
    processor = GatedProcessor()
    engine = FakeEngine()
    consumer = Consumer(OneMessageQueue(), processor, wait_seconds=0, window_seconds=0.001)
    serving = Serving(fake_runtime(settings, meter, OneMessageQueue(), consumer, engine))
    base = await serving.base()
    await asyncio.wait_for(processor.started.wait(), timeout=2)
    serving.stop.set()
    await asyncio.sleep(0.02)  # well inside the 0.2 s grace
    assert not serving.task.done()  # serve() is waiting for the in-hand batch
    processor.gate.set()
    await asyncio.wait_for(serving.task, timeout=2)
    assert processor.completed and not processor.cancelled
    assert engine.disposed
    with pytest.raises(httpx.ConnectError):
        await status(base, "/healthz")


async def test_batch_exceeding_grace_is_cancelled_then_server_closed_and_engine_disposed(
    settings, meter
):
    processor = GatedProcessor()  # gate never opened
    engine = FakeEngine()
    consumer = Consumer(OneMessageQueue(), processor, wait_seconds=0, window_seconds=0.001)
    serving = Serving(fake_runtime(settings, meter, OneMessageQueue(), consumer, engine))
    base = await serving.base()
    await asyncio.wait_for(processor.started.wait(), timeout=2)
    await serving.finish()
    assert processor.cancelled and not processor.completed
    assert engine.disposed
    with pytest.raises(httpx.ConnectError):
        await status(base, "/healthz")


async def test_serve_still_shuts_telemetry_down_when_engine_dispose_fails(settings, meter):
    class FailingEngine(FakeEngine):
        async def dispose(self) -> None:
            raise RuntimeError("dispose failed")

    calls: list[str] = []
    rt = runtime(
        settings, meter, IdleQueue(), Consumer(IdleQueue(), HangingProcessor(), wait_seconds=0)
    )
    rt.engine = FailingEngine()  # type: ignore[assignment]
    rt.uninstrument_engine = lambda: calls.append("uninstrument")
    rt.telemetry_shutdown = lambda: calls.append("telemetry")
    serving = Serving(rt)
    await serving.base()
    with pytest.raises(RuntimeError, match="dispose failed"):
        await serving.finish()
    assert calls == ["uninstrument", "telemetry"]
