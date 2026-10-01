"""Shared integration fixtures. Nothing here is autouse: a fixture only starts containers when a
test (or a conftest wrapper) asks for it."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

# libs/shortener-testing/src/shortener_testing/fixtures.py → repo root is parents[4]
REPO_ROOT = Path(__file__).resolve().parents[4]
API_DIR = REPO_ROOT / "api"
# Keep in sync with docker-compose.yml.
POSTGRES_IMAGE = "postgres:16.10-alpine"
ELASTICMQ_IMAGE = "softwaremill/elasticmq-native:1.6.14"
QUEUES = ("click-events", "click-events-dlq")

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
        if result.exit_code != 0:
            raise RuntimeError(f"bootstrap.sql failed: {result.output.decode()}")
        yield PgServer(container.get_container_host_ip(), int(container.get_exposed_port(5432)))


@pytest.fixture(scope="session")
def make_alembic_config() -> Callable[..., Config]:
    """Test modules can't import each other under importlib mode; share helpers as fixtures."""
    return alembic_config


@pytest.fixture(scope="session")
def migrated(pg_server: PgServer) -> PgServer:
    command.upgrade(alembic_config(pg_server), "head")
    return pg_server


@pytest.fixture
def reset_database(migrated: PgServer) -> None:
    """Return the database to its post-migration state. Wrap it in an autouse fixture per suite."""
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
        if row is None:
            raise RuntimeError("INSERT ... RETURNING id returned no row")
        return UUID(str(row[0]))

    return _insert


@pytest.fixture(scope="session")
def elasticmq() -> Iterator[str]:
    """ElasticMQ with the repo's queue config; yields the endpoint URL."""
    container = (
        DockerContainer(ELASTICMQ_IMAGE)
        .with_exposed_ports(9324)
        .with_volume_mapping(
            str(REPO_ROOT / "infra" / "elasticmq" / "elasticmq.conf"), "/opt/elasticmq.conf", "ro"
        )
        .waiting_for(LogMessageWaitStrategy("started").with_startup_timeout(30))
    )
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9324)}"


@pytest.fixture
def insert_clicks(migrated: PgServer) -> Callable[..., None]:
    def _insert(link_id: UUID, bucket_start: Any, count: int) -> None:
        with migrated.connect("migrator") as conn:
            conn.execute(
                "INSERT INTO analytics.link_clicks_hourly VALUES (%s, %s, %s)",
                (link_id, bucket_start, count),
            )

    return _insert


@pytest.fixture
def insert_referrer(migrated: PgServer) -> Callable[..., None]:
    def _insert(link_id: UUID, bucket_date: Any, host: str, count: int) -> None:
        with migrated.connect("migrator") as conn:
            conn.execute(
                "INSERT INTO analytics.link_referrers_daily VALUES (%s, %s, %s, %s)",
                (link_id, bucket_date, host, count),
            )

    return _insert


@pytest.fixture
def set_data_as_of(migrated: PgServer) -> Callable[[Any], None]:
    def _set(value: Any) -> None:
        with migrated.connect("migrator") as conn:
            conn.execute("UPDATE analytics.pipeline_status SET last_committed_at = %s", (value,))

    return _set


@pytest.fixture
def sqs_client(elasticmq: str) -> Any:
    client = boto3.client(
        "sqs",
        endpoint_url=elasticmq,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",  # noqa: S106 - dummy ElasticMQ credential
    )
    for name in QUEUES:
        client.purge_queue(QueueUrl=client.get_queue_url(QueueName=name)["QueueUrl"])
    return client
