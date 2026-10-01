import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import boto3
import httpx
from botocore.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from shortener_api.auth import HttpJwksProvider, TokenValidator
from shortener_api.clock import SystemClock
from shortener_api.deps import AppDeps
from shortener_api.errors import install_error_handlers
from shortener_api.publisher import BufferedClickPublisher, SqsBatchSender
from shortener_api.routes import health, links, redirect, stats
from shortener_api.settings import ApiSettings, load_api_settings
from shortener_api.telemetry import ApiTelemetry, configure_meter_provider


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    deps: AppDeps = app.state.deps
    await deps.publisher.start()
    try:
        yield
    finally:
        await deps.publisher.stop()
        await deps.engine.dispose()


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
    return app


def build_deps(settings: ApiSettings) -> AppDeps:
    """Real implementations. Nothing here touches the network until first use."""
    clock = SystemClock()
    telemetry = ApiTelemetry(configure_meter_provider(settings).get_meter("shortener_api"))
    jwks = HttpJwksProvider(
        f"{settings.oidc_internal_url}/protocol/openid-connect/certs",
        httpx.AsyncClient(timeout=5.0),
        clock,
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
        telemetry,
        maxsize=settings.click_buffer_size,
        flush_interval=settings.click_flush_interval_seconds,
    )
    return AppDeps(
        settings=settings,
        engine=create_async_engine(str(settings.database_url), pool_pre_ping=True),
        clock=clock,
        rng=secrets.SystemRandom(),
        token_validator=TokenValidator(jwks, settings.oidc_issuer, settings.oidc_audience),
        telemetry=telemetry,
        publisher=publisher,
    )


def create_app_from_env() -> FastAPI:
    """uvicorn --factory entrypoint."""
    return create_app(build_deps(load_api_settings()))
