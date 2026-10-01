"""Unit-level deps: settings plus an engine pointing at a closed port (no Docker needed)."""

import random
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shortener_api.auth import StaticJwksProvider, TokenValidator
from shortener_api.deps import AppDeps
from shortener_api.publisher import InMemoryClickPublisher
from shortener_api.settings import ApiSettings
from shortener_api.telemetry import ApiTelemetry

ISSUER = "http://localhost:8080/realms/shortener"


@pytest.fixture
def settings() -> ApiSettings:
    return ApiSettings(
        database_url="postgresql+psycopg://api_user:x@127.0.0.1:1/shortener",
        public_base_url="http://sho.rt",
        oidc_issuer=ISSUER,
        oidc_internal_url="http://keycloak.invalid/realms/shortener",
    )


@pytest.fixture
async def dead_engine(settings: ApiSettings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(str(settings.database_url))
    yield engine
    await engine.dispose()


@pytest.fixture
def deps(settings, dead_engine, clock, signing_key, meter) -> AppDeps:
    validator = TokenValidator(
        StaticJwksProvider({"test-key": signing_key.public_key()}),
        settings.oidc_issuer,
        settings.oidc_audience,
    )
    return AppDeps(
        settings=settings,
        engine=dead_engine,
        clock=clock,
        rng=random.Random(7),
        token_validator=validator,
        telemetry=ApiTelemetry(meter),
        publisher=InMemoryClickPublisher(),
    )
