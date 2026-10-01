import random
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from shortener_api.auth import StaticJwksProvider, TokenValidator
from shortener_api.deps import AppDeps
from shortener_api.settings import ApiSettings
from shortener_api.telemetry import ApiTelemetry

# Keep in sync with docker-compose.yml.
POSTGRES_IMAGE = "postgres:16.10-alpine"
REPO_ROOT = Path(__file__).resolve().parents[3]
API_DIR = REPO_ROOT / "api"
PASSWORDS = {
    "postgres": "postgres-test",
    "migrator": "migrator-test",
    "api_user": "api-test",
    "processor_user": "processor-test",
    "admin_user": "admin-test",
    "keycloak": "keycloak-test",
}


@dataclass(frozen=True)
class PgServer:
    host: str
    port: int

    def url(self, user: str, db: str = "shortener") -> str:
        return f"postgresql+psycopg://{user}:{PASSWORDS[user]}@{self.host}:{self.port}/{db}"

    def connect(self, user: str, db: str = "shortener") -> psycopg.Connection[Any]:
        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=user,
            password=PASSWORDS[user],
            dbname=db,
            autocommit=True,
        )


def alembic_config(server: PgServer, db: str = "shortener") -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", server.url("migrator", db))
    return cfg


@pytest.fixture(scope="session")
def pg_server() -> Iterator[PgServer]:
    container = PostgresContainer(
        POSTGRES_IMAGE, username="postgres", password=PASSWORDS["postgres"], dbname="postgres"
    ).with_volume_mapping(str(REPO_ROOT / "infra" / "postgres"), "/bootstrap", "ro")
    with container:
        result = container.exec(
            [
                "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
                "-v", f"migrator_pw={PASSWORDS['migrator']}",
                "-v", f"api_pw={PASSWORDS['api_user']}",
                "-v", f"processor_pw={PASSWORDS['processor_user']}",
                "-v", f"admin_pw={PASSWORDS['admin_user']}",
                "-v", f"keycloak_pw={PASSWORDS['keycloak']}",
                "-f", "/bootstrap/bootstrap.sql",
            ]
        )  # fmt: skip
        assert result.exit_code == 0, result.output.decode()
        yield PgServer(container.get_container_host_ip(), int(container.get_exposed_port(5432)))


@pytest.fixture(scope="session")
def make_alembic_config() -> Callable[..., Config]:
    """Test modules can't import each other under importlib mode; share helpers as fixtures."""
    return alembic_config


@pytest.fixture(scope="session")
def migrated(pg_server: PgServer) -> PgServer:
    command.upgrade(alembic_config(pg_server), "head")
    return pg_server


@pytest.fixture(autouse=True)
def _reset_database(migrated: PgServer) -> None:
    """Every test starts from the post-migration state (spec §15.2: tests must not share rows)."""
    with migrated.connect("migrator") as conn:
        conn.execute("TRUNCATE public.links CASCADE")  # cascades to analytics rollups
        conn.execute("UPDATE analytics.pipeline_status SET last_committed_at = NULL")


@pytest.fixture
def insert_link(migrated: PgServer) -> Callable[..., UUID]:
    def _insert(**columns: Any) -> UUID:
        values: dict[str, Any] = {
            "code": uuid4().hex[:7],
            "target_url": "https://example.com/",
            "owner_sub": "sub-eddie",
            "owner_username": "eddie",
        }
        values.update(columns)
        names = ", ".join(values)
        params = ", ".join(f"%({k})s" for k in values)
        # Test-only SQL: column names come from the literal keys above, never from input.
        sql = f"INSERT INTO public.links ({names}) VALUES ({params}) RETURNING id"  # noqa: S608
        with migrated.connect("migrator") as conn:
            row = conn.execute(sql, values).fetchone()
        assert row is not None
        return UUID(str(row[0]))

    return _insert


ISSUER = "http://localhost:8080/realms/shortener"


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
    )
