"""Service lifecycle: consumer + queue-depth poller + health server; graceful SIGTERM."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import boto3
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shortener_processor.batch import BatchProcessor
from shortener_processor.consumer import Consumer
from shortener_processor.health import start_health_server
from shortener_processor.link_resolver import PostgresLinkResolver
from shortener_processor.queue import QueueClient
from shortener_processor.rollup_store import PostgresRollupStore
from shortener_processor.settings import ProcessorSettings, load_processor_settings
from shortener_processor.sqs import SQS_CONFIG, SqsQueueClient
from shortener_processor.telemetry import ProcessorTelemetry, configure_meter_provider

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Runtime:
    settings: ProcessorSettings
    engine: AsyncEngine
    queue: QueueClient
    consumer: Consumer
    telemetry: ProcessorTelemetry


def build_runtime(settings: ProcessorSettings) -> Runtime:
    telemetry = ProcessorTelemetry(
        configure_meter_provider(settings).get_meter("shortener_processor")
    )
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    sqs = boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.sqs_endpoint_url,
        config=SQS_CONFIG,
    )
    queue = SqsQueueClient(sqs, settings.click_events_queue_name)
    processor = BatchProcessor(
        queue,
        PostgresLinkResolver(engine, ttl_seconds=settings.link_cache_ttl_seconds),
        PostgresRollupStore(engine),
        telemetry,
    )
    consumer = Consumer(
        queue,
        processor,
        max_messages=settings.batch_max_messages,
        window_seconds=settings.batch_window_seconds,
        wait_seconds=settings.receive_wait_seconds,
    )
    return Runtime(
        settings=settings, engine=engine, queue=queue, consumer=consumer, telemetry=telemetry
    )


async def poll_queue_depth(
    queue: QueueClient,
    names: Sequence[str],
    telemetry: ProcessorTelemetry,
    interval: float,
    stop: asyncio.Event,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while not stop.is_set():
        for name in names:
            try:
                telemetry.set_queue_depth(name, await queue.depth(name))
            except Exception:
                logger.warning("queue depth poll failed for %s", name, exc_info=True)
        await sleep(interval)


async def serve(
    runtime: Runtime,
    stop: asyncio.Event,
    *,
    on_started: Callable[[asyncio.Server], None] | None = None,
) -> None:
    settings = runtime.settings
    stale_after = max(10.0, 3.0 * settings.receive_wait_seconds)
    consumer_task = asyncio.create_task(runtime.consumer.run(stop), name="consumer")
    poller_task = asyncio.create_task(
        poll_queue_depth(
            runtime.queue,
            [settings.click_events_queue_name, settings.click_events_dlq_name],
            runtime.telemetry,
            settings.queue_depth_interval_seconds,
            stop,
        ),
        name="queue-depth",
    )

    async def live() -> bool:
        return not consumer_task.done() and runtime.consumer.seconds_since_heartbeat() < stale_after

    async def ready() -> bool:
        try:
            async with runtime.engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return False
        return True

    server = await start_health_server(
        settings.health_host, settings.health_port, live=live, ready=ready
    )
    if on_started is not None:
        on_started(server)
    try:
        await stop.wait()
    finally:
        try:
            await asyncio.wait_for(asyncio.shield(consumer_task), settings.shutdown_grace_seconds)
        except TimeoutError:
            logger.warning(
                "batch still running after %.0fs grace; abandoning it",
                settings.shutdown_grace_seconds,
            )
            consumer_task.cancel()  # uncommitted transaction rolls back; nothing was deleted
        for task in (consumer_task, poller_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        server.close()
        await server.wait_closed()
        await runtime.engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    runtime = build_runtime(load_processor_settings())

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await serve(runtime, stop)

    asyncio.run(_run())
