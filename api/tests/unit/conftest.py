"""Unit-level deps: settings plus an engine pointing at a closed port (no Docker needed)."""

import random
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shortener_api.deps import AppDeps
from shortener_api.settings import ApiSettings

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
def deps(settings, dead_engine, clock) -> AppDeps:
    return AppDeps(settings=settings, engine=dead_engine, clock=clock, rng=random.Random(7))
