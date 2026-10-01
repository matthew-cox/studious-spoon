"""API integration fixtures. Shared Postgres/ElasticMQ fixtures come from shortener_testing."""

import random
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shortener_api.auth import StaticJwksProvider, TokenValidator
from shortener_api.deps import AppDeps
from shortener_api.publisher import InMemoryClickPublisher
from shortener_api.settings import ApiSettings
from shortener_api.telemetry import ApiTelemetry
from shortener_testing.fixtures import PgServer

ISSUER = "http://localhost:8080/realms/shortener"


@pytest.fixture(autouse=True)
def _reset_database(reset_database: None) -> None:
    """Every api integration test starts from the post-migration state (spec §15.2)."""


@pytest.fixture
def api_settings(migrated: PgServer) -> ApiSettings:
    return ApiSettings(
        database_url=migrated.url("api_user"),
        public_base_url="http://sho.rt",
        oidc_issuer=ISSUER,
        oidc_internal_url="http://keycloak.invalid/realms/shortener",
    )


@pytest.fixture
async def engine(api_settings: ApiSettings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(str(api_settings.database_url))
    yield engine
    await engine.dispose()


@pytest.fixture
def deps(api_settings, engine, clock, signing_key, meter) -> AppDeps:
    validator = TokenValidator(
        StaticJwksProvider({"test-key": signing_key.public_key()}),
        api_settings.oidc_issuer,
        api_settings.oidc_audience,
    )
    return AppDeps(
        settings=api_settings,
        engine=engine,
        clock=clock,
        rng=random.Random(7),
        token_validator=validator,
        telemetry=ApiTelemetry(meter),
        publisher=InMemoryClickPublisher(),
    )
