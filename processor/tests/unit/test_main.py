import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from shortener_processor.batch import BatchOutcome
from shortener_processor.consumer import Consumer
from shortener_processor.main import Runtime, build_runtime, poll_queue_depth, serve
from shortener_processor.queue import ReceivedMessage
from shortener_processor.sqs import SqsQueueClient
from shortener_processor.telemetry import ProcessorTelemetry, configure_meter_provider


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
    built = build_runtime(settings)
    assert isinstance(built.queue, SqsQueueClient)
    assert isinstance(built.consumer, Consumer)
    await built.engine.dispose()


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


def test_meter_provider_resource(settings):
    provider = configure_meter_provider(settings)
    attributes = provider._sdk_config.resource.attributes  # private: SDK has no public accessor
    assert attributes["service.name"] == "shortener-click-processor"
    provider.shutdown()
