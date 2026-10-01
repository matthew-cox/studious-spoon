import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import boto3
import httpx
from botocore.config import Config
from fastapi import FastAPI
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy.ext.asyncio import create_async_engine

from shortener_api.auth import HttpJwksProvider, TokenValidator
from shortener_api.clock import SystemClock
from shortener_api.deps import AppDeps
from shortener_api.errors import install_error_handlers
from shortener_api.publisher import BufferedClickPublisher, SqsBatchSender
from shortener_api.routes import health, links, redirect, stats
from shortener_api.settings import ApiSettings, load_api_settings
from shortener_api.telemetry import ApiTelemetry
from shortener_observability import configure_telemetry


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    deps: AppDeps = app.state.deps
    await deps.publisher.start()
    try:
        yield
    finally:
        await deps.publisher.stop()
        await deps.engine.dispose()
        if deps.telemetry_shutdown is not None:
            deps.telemetry_shutdown()


def create_app(deps: AppDeps) -> FastAPI:
    app = FastAPI(
        title="URL Shortener API", version=deps.settings.service_version, lifespan=_lifespan
    )
    app.state.deps = deps
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(links.router)
    app.include_router(stats.router)
    app.include_router(redirect.router)  # last: /{code} must not shadow real routes
    if deps.tracer_provider is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=deps.tracer_provider,
            meter_provider=deps.meter_provider,
            excluded_urls="healthz,readyz",
        )
        SQLAlchemyInstrumentor().instrument(
            engine=deps.engine.sync_engine, tracer_provider=deps.tracer_provider
        )
    return app


def build_deps(settings: ApiSettings, *, install_globals: bool = True) -> AppDeps:
    """Real implementations. Nothing here touches the network until first use."""
    telemetry = configure_telemetry(
        service_name="shortener-api",
        service_version=settings.service_version,
        environment=settings.deployment_environment,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        metric_export_interval_ms=settings.otel_metric_export_interval_ms,
        log_level=settings.log_level,
        install_globals=install_globals,
    )
    clock = SystemClock()
    api_telemetry = ApiTelemetry(telemetry.meter("shortener_api"))
    jwks_http = httpx.AsyncClient(timeout=5.0)
    HTTPXClientInstrumentor.instrument_client(jwks_http, tracer_provider=telemetry.tracer_provider)
    if install_globals:  # botocore instrumentation patches the library globally
        BotocoreInstrumentor().instrument(  # type: ignore[no-untyped-call]  # upstream is untyped
            tracer_provider=telemetry.tracer_provider
        )
    jwks = HttpJwksProvider(
        f"{settings.oidc_internal_url}/protocol/openid-connect/certs", jwks_http, clock
    )
    sqs = boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.sqs_endpoint_url,
        # Bounded: the publisher owns retries and a 5 s shutdown drain (botocore defaults: 60 s, 5).
        config=Config(
            connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1, "mode": "standard"}
        ),
    )
    publisher = BufferedClickPublisher(
        SqsBatchSender(sqs, settings.click_events_queue_name),
        api_telemetry,
        maxsize=settings.click_buffer_size,
        flush_interval=settings.click_flush_interval_seconds,
    )
    return AppDeps(
        settings=settings,
        engine=create_async_engine(str(settings.database_url), pool_pre_ping=True),
        clock=clock,
        rng=secrets.SystemRandom(),
        token_validator=TokenValidator(jwks, settings.oidc_issuer, settings.oidc_audience),
        telemetry=api_telemetry,
        publisher=publisher,
        tracer_provider=telemetry.tracer_provider,
        meter_provider=telemetry.meter_provider,
        telemetry_shutdown=telemetry.shutdown,
    )


def create_app_from_env() -> FastAPI:
    """uvicorn --factory entrypoint."""
    return create_app(build_deps(load_api_settings()))
