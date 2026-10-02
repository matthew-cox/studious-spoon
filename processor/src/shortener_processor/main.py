"""Service lifecycle: consumer + queue-depth poller + health server; graceful SIGTERM."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import boto3
import sqlalchemy as sa
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shortener_observability import configure_telemetry, instrument_engine
from shortener_processor.batch import BatchProcessor
from shortener_processor.consumer import Consumer
from shortener_processor.health import start_health_server
from shortener_processor.link_resolver import PostgresLinkResolver
from shortener_processor.queue import QueueClient
from shortener_processor.rollup_store import PostgresRollupStore
from shortener_processor.settings import ProcessorSettings, load_processor_settings
from shortener_processor.sqs import SQS_CONFIG, SqsQueueClient
from shortener_processor.telemetry import ProcessorTelemetry

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Runtime:
    settings: ProcessorSettings
    engine: AsyncEngine
    queue: QueueClient
    consumer: Consumer
    telemetry: ProcessorTelemetry
    tracer_provider: TracerProvider | None = None
    telemetry_shutdown: Callable[[], None] | None = None
    uninstrument_engine: Callable[[], None] | None = None


def build_runtime(settings: ProcessorSettings, *, install_globals: bool = True) -> Runtime:
    telemetry = configure_telemetry(
        service_name="shortener-click-processor",
        service_version=settings.service_version,
        environment=settings.deployment_environment,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        metric_export_interval_ms=settings.otel_metric_export_interval_ms,
        log_level=settings.log_level,
        install_globals=install_globals,
        max_span_links=settings.batch_max_messages,
    )
    processor_telemetry = ProcessorTelemetry(telemetry.meter("shortener_processor"))
    engine = create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        # A blackholed DB must fail fast, and a slow commit must stay inside the 30 s
        # SQS visibility timeout.
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=20000"},
    )
    uninstrument_engine = instrument_engine(
        engine.sync_engine, telemetry.tracer_provider, telemetry.meter_provider
    )
    if install_globals:  # botocore instrumentation patches the library globally
        BotocoreInstrumentor().instrument(  # type: ignore[no-untyped-call]  # upstream is untyped
            tracer_provider=telemetry.tracer_provider
        )
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
        processor_telemetry,
        tracer=telemetry.tracer("shortener_processor"),
        destination_name=settings.click_events_queue_name,
    )
    consumer = Consumer(
        queue,
        processor,
        max_messages=settings.batch_max_messages,
        window_seconds=settings.batch_window_seconds,
        wait_seconds=settings.receive_wait_seconds,
    )
    return Runtime(
        settings=settings,
        engine=engine,
        queue=queue,
        consumer=consumer,
        telemetry=processor_telemetry,
        tracer_provider=telemetry.tracer_provider,
        telemetry_shutdown=telemetry.shutdown,
        uninstrument_engine=uninstrument_engine,
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
        try:
            await runtime.engine.dispose()
        finally:
            try:
                if runtime.uninstrument_engine is not None:
                    runtime.uninstrument_engine()
            finally:
                if runtime.telemetry_shutdown is not None:
                    runtime.telemetry_shutdown()


def main() -> None:
    runtime = build_runtime(load_processor_settings())

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await serve(runtime, stop)

    asyncio.run(_run())
