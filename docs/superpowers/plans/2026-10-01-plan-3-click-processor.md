# Plan 3 — Click Processor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `click-processor` service. It long-polls the `click-events` SQS queue, validates and resolves each `link.clicked` event, aggregates a batch into hourly click and daily referrer rollups, upserts them in one Postgres transaction, and only then deletes the messages. Poison messages are left for the DLQ. The processor runs in compose, and the API's stats endpoints show real click counts end to end.

**Architecture:** A new workspace package, `processor/` (`shortener_processor`).
- **Pure core:** `referrers.py` (Referer → host) and `aggregate.py` (clicks → rollup deltas).
- **Protocol-typed adapters**, each with a hand-written fake for tests:
  - `QueueClient`: SQS via boto3 + `asyncio.to_thread`
  - `LinkResolver`: Postgres, with a TTL cache
  - `RollupStore`: Postgres upserts
- **`BatchProcessor`** orchestrates one batch: decode → resolve → aggregate → commit → delete.
- **`Consumer`** collects up to 100 messages within a ~1 s window.
- **`main.serve()`** runs the consumer, a queue-depth poller, and a tiny stdlib health server, and shuts down gracefully on SIGTERM.

**Tech Stack:** Python 3.12, SQLAlchemy 2 async + psycopg 3, boto3/botocore, OpenTelemetry metrics SDK (OTLP/HTTP), and the Plan 1 `shortener_events` library. Tests use pytest with testcontainers (Postgres, ElasticMQ), through a new shared pytest plugin, `shortener_testing`.

**Spec:** `docs/superpowers/specs/2026-10-01-url-shortener-design.md`. Read §2 (D11–D13), §3.2–3.4, §4.2, §5.1, §5.3, §5.4, §9 ("Processor failures", "DB unavailable"), §10 (processor metrics), §11, and §15 before starting.

**Plan series:** 1 Foundation (done) → 2 API (done) → **3 Click processor (this)** → 4 Admin UI → 5 Observability (tracing incl. SQS span links, JSON logs, dashboard), E2E, CI hardening.

**Scope split with Plan 5 (intentional):** this plan builds the processor's **metrics**. The `process click batch` span with span links to producers' `traceparent` (spec §5.3) lands in Plan 5, along with tracing for every service.

**Suggested execution batches** (batched subagent-driven execution): (Tasks 1–2), (3–4), (5–6), (7).

**Deviations from the spec (intentional; Task 7 records them in the spec):**
1. **The foreign-key race fails the batch instead of skipping rows.**
   - **Spec §5.3 step 4** says rows whose link "was deleted at the same moment are skipped if they violate the foreign key."
   - **What this plan does instead:** it checks link existence inside the transaction and skips links that are already gone. A delete that lands in the tiny window *after* that check makes the transaction fail with a foreign-key violation. That batch is then retried after the visibility timeout, and on the retry the link is gone, so those events count as `unknown_link`.
   - **Why:** `processor_user` can't take row locks on `links`, because Postgres requires `UPDATE` privilege for `FOR KEY SHARE`. Failing and retrying gives the same end state without widening the grants.
2. **Filenames differ from the §3.3 layout.**
   - Files renamed: `settings.py` instead of `config.py`, matching the API.
   - Files added: `referrers.py`, `batch.py`, `sqs.py`, `health.py`, `db.py`.
   - Task 7 updates §3.3.
3. **Duplicate deliveries inside one batch are dropped.** Events with the same `event_id` in a single batch (an SQS duplicate delivery) are counted once, and every copy is deleted. This narrows D13's accepted overcount at no cost. It isn't a replacement for §12's short-window dedupe.

## Global Constraints

- Everything in Plans 1–2's Global Constraints still applies:
  - config only from the environment, failing fast
  - pinned images
  - no SQLite; no `sleep` in unit/integration tests
  - fakes over mocks
  - `mypy --strict` on `src/`
  - coverage ≥ 80% overall and ≥ 90% on pure modules
  - Run `uv run ruff format . && make check` before every commit, chained with `&&`, and never piped through `tail`/`head`. Never commit on a red gate.
- Commit trailer: the implementing model's own name, e.g. `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Pure modules:** `shortener_processor/referrers.py` and `shortener_processor/aggregate.py` must not import SQLAlchemy, boto, httpx, or OpenTelemetry. Add both to `PURE_MODULES`.
- ruff `S101` forbids `assert` in `src/`. Use `typing.cast` or explicit raises. Tests may assert.
- Queue names are `click-events` and `click-events-dlq`. The visibility timeout is 30 s and `maxReceiveCount=5` (both from `infra/elasticmq/elasticmq.conf`; don't change them).
- Batch limits:
  - **≤ 100 messages per batch.** Collection stops ~1 s after the first message arrives.
  - **Each `ReceiveMessage`:** `MaxNumberOfMessages ≤ 10`, and the first receive of a batch uses `WaitTimeSeconds=20`.
  - **botocore:** the SQS client's `read_timeout` must exceed the 20 s long poll.
- Ordering and outcomes:
  - **Order:** commit first, then delete. Never delete a message whose effects weren't committed.
  - **Invalid or unknown-version messages** are never deleted. They're counted as `result="invalid"` and redriven to the DLQ by SQS.
  - **Unknown or deleted links** are counted as `result="unknown_link"` and deleted.
- **Rollup writes:**
  - `INSERT … ON CONFLICT DO UPDATE SET count = count + excluded.count`
  - rows sorted by primary key, to avoid deadlocks between processor replicas
  - `analytics.pipeline_status.last_committed_at` is set in the same transaction
- **Buckets** use the event's `occurred_at`, in UTC: an hour for clicks, a date for referrers. The referrer host is the lower-cased hostname, or `(direct)`.
- **Metrics** (spec §10), exactly:
  - `shortener.processor.messages{result=ok|invalid|unknown_link}`
  - `shortener.processor.batch.duration` (s)
  - `shortener.processor.event_lag` (s, from `occurred_at` to commit)
  - `shortener.queue.depth{queue=click-events|click-events-dlq}`, polled every 30 s

  No per-link attributes.
- **The processor's database user is `processor_user`.** It can read `public.links (id, code)` and read/write `analytics.*`; nothing else.
- **Health server:** `:8002`, with `/healthz` (the consumer loop is alive and its heartbeat is fresh) and `/readyz` (the DB answers). Its output is JSON.
- Shutdown on SIGTERM:
  - stop receiving
  - let an in-hand batch finish within a 25 s grace period
  - then cancel; an uncommitted transaction rolls back and nothing is deleted
  - compose sets `stop_grace_period: 30s`
- **New workspace packages** (`libs/shortener-testing`, `processor`) must be added to the root workspace `members`. Each one's `pyproject.toml` must be copied in **every** Dockerfile's dependency layer.
- Integration tests on colima need `export DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock" TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`. Also `export VIRTUAL_ENV=`, because a stray pyenv venv is active.

## Review Focus

1. **A poison message in a batch of good ones.** Valid messages in the same batch are still committed and deleted. The poison message stays on the queue, and after 5 receives it ends up in `click-events-dlq`. It never aborts the batch or the loop. Tests: Task 4 `test_invalid_message_is_counted_and_left_on_the_queue`; Task 5 `test_poison_message_is_redriven_to_the_dlq` and `test_end_to_end_batch_against_real_queue_and_db`.
2. **A link deleted between the redirect and processing.** Its events count as `unknown_link` and are deleted, while other links' counts in the same batch are committed. Tests: Task 3 `test_commit_skips_links_that_no_longer_exist`; Task 4 `test_store_skipped_links_count_as_unknown_and_are_deleted`.
3. **Database down mid-batch.** Nothing is deleted, the exception doesn't kill the loop, and the messages come back after the visibility timeout. Tests: Task 4 `test_commit_failure_deletes_nothing`; Task 5 `test_run_survives_a_failing_batch`.
4. **Odd metadata:**
   - `Referer` missing, empty, scheme-less, `android-app://…`, malformed brackets, or upper case
   - `occurred_at` in a non-UTC offset just before midnight

   Each lands in the correct host, hour, and date bucket. Tests: Task 2 `test_referrers.py` and `test_aggregate.py`.
5. **SIGTERM, both mid-long-poll and mid-batch.** Shutdown finishes within the grace period, and a batch that didn't commit deletes nothing. Tests: Task 6 `test_serve_stops_on_signal_event` and `test_shutdown_cancels_a_batch_that_exceeds_the_grace_period`.

---

## File Structure

```
libs/shortener-testing/                 NEW shared pytest plugin (entry point pytest11)
  pyproject.toml
  src/shortener_testing/__init__.py
  src/shortener_testing/fixtures.py     PgServer, pg_server, migrated, reset_database, insert_link,
                                        insert_clicks, insert_referrer, set_data_as_of, elasticmq, sqs_client
api/tests/integration/conftest.py       slimmed: autouse wrapper + api-specific fixtures only
processor/                              NEW workspace member `shortener-processor`
  pyproject.toml, Dockerfile
  src/shortener_processor/
    __init__.py, __main__.py, py.typed
    settings.py         ProcessorSettings, load_processor_settings()
    referrers.py        (pure) referrer_host()
    aggregate.py        (pure) ResolvedClick, RollupDeltas, hour_bucket(), aggregate()
    db.py               SQLAlchemy Core tables the processor touches
    rollup_store.py     CommitResult, RollupStore, PostgresRollupStore
    link_resolver.py    LinkResolver, PostgresLinkResolver (TTL cache)
    queue.py            ReceivedMessage, QueueClient protocol
    sqs.py              SQS_CONFIG, SqsQueueClient
    telemetry.py        ProcessorTelemetry, configure_meter_provider()
    batch.py            BatchOutcome, BatchProcessor
    consumer.py         Consumer (collect window, run loop, heartbeat)
    health.py           start_health_server() — stdlib asyncio HTTP
    main.py             Runtime, build_runtime(), poll_queue_depth(), serve(), main()
  tests/unit/...  tests/integration/...
docker-compose.yml, Makefile, pyproject.toml (root), .github/workflows/ci.yml, api/Dockerfile,
tools/keycloak-tools/Dockerfile          wiring for the new packages
tests/e2e/test_api.py                   click assertion now via stats (processor consumes the queue)
tests/e2e/test_processor.py             NEW: counts end to end; stopped processor → backlog → drain
README.md, spec                         docs
```

---

### Task 1: Shared integration-test fixtures as a pytest plugin

**Files:**
- Create: `libs/shortener-testing/pyproject.toml`, `libs/shortener-testing/src/shortener_testing/__init__.py`, `libs/shortener-testing/src/shortener_testing/fixtures.py`
- Modify: `api/tests/integration/conftest.py` (slimmed), root `pyproject.toml` (members, dev group, sources), `api/Dockerfile` and `tools/keycloak-tools/Dockerfile` (dependency-layer COPY line)
- Test: the existing api suite (unchanged test count) is the regression test.

**Interfaces:**
- Produces (pytest fixtures available to every test in the workspace via the `pytest11` entry point; none are autouse):
  - `PgServer(host, port)` with `.url(user, db="shortener")` and `.connect(user, db="shortener")`; also importable as `shortener_testing.fixtures.PgServer`
  - `PASSWORDS`
  - `pg_server` (session)
  - `make_alembic_config` (session)
  - `migrated` (session)
  - `reset_database`, which truncates `public.links CASCADE` and nulls `pipeline_status.last_committed_at`
  - `insert_link(**columns) -> UUID`
  - `insert_clicks(link_id, bucket_start, count)`
  - `insert_referrer(link_id, bucket_date, host, count)`
  - `set_data_as_of(value)`
  - `elasticmq` (session; yields the endpoint URL)
  - `sqs_client`, which purges **both** `click-events` and `click-events-dlq`

- [ ] **Step 1: Record the baseline**

Run: `uv run pytest api -q 2>&1 | tail -1`. Write down the pass count. It must be identical after this task. (The `tail` here only reads a count; it isn't gating a commit.)

- [ ] **Step 2: Create the plugin package**

`libs/shortener-testing/pyproject.toml`:
```toml
[project]
name = "shortener-testing"
version = "0.1.0"
description = "Shared pytest fixtures (Postgres + Alembic, ElasticMQ) for workspace integration tests."
requires-python = ">=3.12"
dependencies = [
  "pytest>=8.3",
  "testcontainers[postgres]>=4.8",
  "psycopg[binary]>=3.2",
  "alembic>=1.13",
  "boto3>=1.35",
]

[project.entry-points.pytest11]
shortener_testing = "shortener_testing.fixtures"

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shortener_testing"]
```

`libs/shortener-testing/src/shortener_testing/__init__.py`:
```python
"""Shared pytest fixtures for workspace integration tests (loaded via the pytest11 entry point)."""
```

`libs/shortener-testing/src/shortener_testing/fixtures.py`. Move these over **verbatim** from `api/tests/integration/conftest.py`: `POSTGRES_IMAGE`, `PASSWORDS`, `PgServer`, `alembic_config`, `pg_server`, `make_alembic_config`, `migrated`, `insert_link`, `ELASTICMQ_IMAGE`, `elasticmq`, `insert_clicks`, `insert_referrer`, and `set_data_as_of`. Then make these changes:
```python
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

# ... PASSWORDS, PgServer, alembic_config, pg_server, make_alembic_config, migrated (verbatim) ...


@pytest.fixture
def reset_database(migrated: PgServer) -> None:
    """Return the database to its post-migration state. Wrap it in an autouse fixture per suite."""
    with migrated.connect("migrator") as conn:
        conn.execute("TRUNCATE public.links CASCADE")  # cascades to analytics rollups
        conn.execute("UPDATE analytics.pipeline_status SET last_committed_at = NULL")


# ... insert_link, elasticmq, insert_clicks, insert_referrer, set_data_as_of (verbatim) ...


@pytest.fixture
def sqs_client(elasticmq: str) -> Any:
    client = boto3.client(
        "sqs",
        endpoint_url=elasticmq,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    for name in QUEUES:
        client.purge_queue(QueueUrl=client.get_queue_url(QueueName=name)["QueueUrl"])
    return client
```
Write the file out in full. The `# ...` lines above mark where the moved code goes; they aren't placeholders to keep.

- [ ] **Step 3: Slim the api conftest**

Replace `api/tests/integration/conftest.py` with:
```python
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
```

- [ ] **Step 4: Register the package in the workspace and Dockerfiles**

Root `pyproject.toml`:
- `members = ["libs/shortener-events", "libs/shortener-testing", "api", "tools/keycloak-tools"]`
- add `"shortener-testing"` to the `dev` dependency group
- add:
```toml
[tool.uv.sources]
shortener-testing = { workspace = true }
```

In **both** `api/Dockerfile` and `tools/keycloak-tools/Dockerfile`, add this next to the other member `COPY` lines in the dependency layer:
```dockerfile
COPY libs/shortener-testing/pyproject.toml libs/shortener-testing/
```

Run: `uv lock && uv sync --all-packages`
Expected: success.

- [ ] **Step 5: Verify that nothing changed behaviourally**

Run (colima env exported): `uv run pytest api -q`
Expected: the same pass count as in Step 1, with no errors.

Run: `uv run pytest tools libs -q`
Expected: passes, and **no containers start** (no fixture is autouse). Check it with `docker ps` during the run, or simply observe that the run takes about a second.

Run: `docker build -q -f api/Dockerfile . && docker build -q -f tools/keycloak-tools/Dockerfile .`
Expected: both build.

- [ ] **Step 6: Gates and commit**

```bash
uv run ruff format . && make check && git add libs/shortener-testing api/tests/integration/conftest.py pyproject.toml uv.lock api/Dockerfile tools/keycloak-tools/Dockerfile && git commit -m "test: extract shared integration fixtures into the shortener_testing pytest plugin

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 2: Processor package, settings, and the pure core (referrers, aggregate)

**Files:**
- Create: `processor/pyproject.toml`, `processor/src/shortener_processor/{__init__,settings,referrers,aggregate}.py`, `processor/src/shortener_processor/py.typed`
- Modify: root `pyproject.toml` (members; coverage `source`), `Makefile` (`MYPY_TARGETS`, `PURE_MODULES`), `api/Dockerfile` and `tools/keycloak-tools/Dockerfile` (dependency-layer COPY line)
- Test: `processor/tests/unit/test_settings.py`, `processor/tests/unit/test_referrers.py`, `processor/tests/unit/test_aggregate.py`

**Interfaces:**
- Consumes: `ClickEvent` (Plan 1) only, indirectly through later tasks.
- Produces:
  - `ProcessorSettings` with fields:
    - `database_url: PostgresDsn`
    - `sqs_endpoint_url: str | None = None`
    - `aws_region: str = "us-east-1"`
    - `click_events_queue_name: str = "click-events"`
    - `click_events_dlq_name: str = "click-events-dlq"`
    - `batch_max_messages: int = 100` (1..1000)
    - `batch_window_seconds: float = 1.0` (> 0)
    - `receive_wait_seconds: int = 20` (0..20)
    - `queue_depth_interval_seconds: float = 30.0` (> 0)
    - `link_cache_ttl_seconds: float = 60.0` (≥ 0)
    - `health_host: str = "0.0.0.0"`
    - `health_port: int = 8002`
    - `shutdown_grace_seconds: float = 25.0` (> 0)
    - `otel_exporter_otlp_endpoint: str | None = None`
    - `deployment_environment: str = "local"`
    - `service_version: str = "dev"`
  - `load_processor_settings() -> ProcessorSettings`
  - `referrers.DIRECT = "(direct)"`, `referrers.MAX_HOST_LENGTH = 255`, and `referrers.referrer_host(referrer: str | None) -> str`
  - `aggregate.ResolvedClick(link_id: UUID, occurred_at: datetime, referrer_host: str)`
  - `aggregate.RollupDeltas(hourly: dict[tuple[UUID, datetime], int], referrers: dict[tuple[UUID, date, str], int])`, with `.link_ids: frozenset[UUID]`, `.is_empty: bool`, and `.without(link_ids) -> RollupDeltas`
  - `aggregate.hour_bucket(ts) -> datetime`
  - `aggregate.aggregate(clicks: Iterable[ResolvedClick]) -> RollupDeltas`

- [ ] **Step 1: Create the package and register it**

`processor/pyproject.toml`:
```toml
[project]
name = "shortener-processor"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "shortener-events",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy[asyncio]>=2.0.35",
  "psycopg[binary]>=3.2",
  "boto3>=1.35",
  "opentelemetry-api>=1.27",
  "opentelemetry-sdk>=1.27",
  "opentelemetry-exporter-otlp-proto-http>=1.27",
]

[tool.uv.sources]
shortener-events = { workspace = true }

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shortener_processor"]
```
Create `processor/src/shortener_processor/__init__.py` with `"""Click processor: SQS link.clicked events → analytics rollups (spec §5.3)."""`, and an empty `py.typed`.

Root `pyproject.toml`:
- add `"processor"` to `members`
- add `"shortener_processor"` to `[tool.coverage.run] source`

`Makefile`:
- append ` processor/src` to `MYPY_TARGETS`
- append `,*/shortener_processor/referrers.py,*/shortener_processor/aggregate.py` to `PURE_MODULES`

In `api/Dockerfile` and `tools/keycloak-tools/Dockerfile`, add `COPY processor/pyproject.toml processor/` to the dependency layer.

Run: `uv lock && uv sync --all-packages`
Expected: success.

- [ ] **Step 2: Write the failing settings tests**

`processor/tests/unit/test_settings.py`:
```python
import pytest
from pydantic import ValidationError

from shortener_processor.settings import load_processor_settings

URL = "postgresql+psycopg://processor_user:pw@db:5432/shortener"


def test_missing_database_url_fails_fast_naming_it(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        load_processor_settings()


def test_defaults_match_the_spec(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    settings = load_processor_settings()
    assert settings.batch_max_messages == 100
    assert settings.batch_window_seconds == 1.0
    assert settings.receive_wait_seconds == 20
    assert settings.queue_depth_interval_seconds == 30.0
    assert settings.click_events_queue_name == "click-events"
    assert settings.click_events_dlq_name == "click-events-dlq"
    assert settings.health_port == 8002


@pytest.mark.parametrize(
    ("name", "value"),
    [("RECEIVE_WAIT_SECONDS", "21"), ("BATCH_MAX_MESSAGES", "0"), ("BATCH_WINDOW_SECONDS", "0")],
)
def test_out_of_range_values_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        load_processor_settings()
```

Run: `uv run pytest processor/tests/unit/test_settings.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_processor.settings'`.

- [ ] **Step 3: Implement the settings**

`processor/src/shortener_processor/settings.py`:
```python
from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProcessorSettings(BaseSettings):
    """Configuration for the click processor (environment only, spec §15.1 III)."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: PostgresDsn
    sqs_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    click_events_queue_name: str = "click-events"
    click_events_dlq_name: str = "click-events-dlq"
    batch_max_messages: int = Field(default=100, ge=1, le=1000)
    batch_window_seconds: float = Field(default=1.0, gt=0)
    receive_wait_seconds: int = Field(default=20, ge=0, le=20)  # SQS long-poll maximum
    queue_depth_interval_seconds: float = Field(default=30.0, gt=0)
    link_cache_ttl_seconds: float = Field(default=60.0, ge=0)
    health_host: str = "0.0.0.0"  # noqa: S104 — the container healthcheck / load balancer must reach it
    health_port: int = 8002
    shutdown_grace_seconds: float = Field(default=25.0, gt=0)
    otel_exporter_otlp_endpoint: str | None = None
    deployment_environment: str = "local"
    service_version: str = "dev"


def load_processor_settings() -> ProcessorSettings:
    return ProcessorSettings()
```

Run: `uv run pytest processor/tests/unit/test_settings.py -q`
Expected: PASS.

- [ ] **Step 4: Write the failing referrer tests**

`processor/tests/unit/test_referrers.py`:
```python
import pytest

from shortener_processor.referrers import DIRECT, MAX_HOST_LENGTH, referrer_host


@pytest.mark.parametrize(
    ("referrer", "expected"),
    [
        ("https://news.ycombinator.com/item?id=1", "news.ycombinator.com"),
        ("HTTPS://News.Example.COM/Path", "news.example.com"),
        ("http://example.com:8080/x", "example.com"),
        ("https://user:pass@example.com/", "example.com"),
        ("https://example.com./", "example.com"),
        ("android-app://com.google.android.gm/", "com.google.android.gm"),
        ("  https://padded.example/  ", "padded.example"),
    ],
)
def test_extracts_lowercased_host(referrer, expected):
    assert referrer_host(referrer) == expected


@pytest.mark.parametrize(
    "referrer", [None, "", "   ", "news.example/no-scheme", "/relative/path", "http://[", "https://"]
)
def test_unusable_referrers_are_direct(referrer):
    assert referrer_host(referrer) == DIRECT


def test_direct_marker_is_spec_value():
    assert DIRECT == "(direct)"


def test_very_long_hosts_are_truncated():
    host = "a" * 300 + ".example"
    assert referrer_host(f"https://{host}/") == host[:MAX_HOST_LENGTH]
```

Run: `uv run pytest processor/tests/unit/test_referrers.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 5: Implement `referrers.py`**

`processor/src/shortener_processor/referrers.py`:
```python
"""Referer header → referrer host for daily rollups (spec §4.2). Pure."""

from urllib.parse import urlsplit

DIRECT = "(direct)"
MAX_HOST_LENGTH = 255


def referrer_host(referrer: str | None) -> str:
    """Lower-cased host of the Referer, or "(direct)" if absent or unparseable."""
    if not referrer or not referrer.strip():
        return DIRECT
    try:
        host = urlsplit(referrer.strip()).hostname
    except ValueError:  # e.g. malformed IPv6 brackets
        return DIRECT
    host = (host or "").rstrip(".")
    return host[:MAX_HOST_LENGTH] if host else DIRECT
```

Run: `uv run pytest processor/tests/unit/test_referrers.py -q`
Expected: PASS.

- [ ] **Step 6: Write the failing aggregation tests**

`processor/tests/unit/test_aggregate.py`:
```python
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

from shortener_processor.aggregate import ResolvedClick, aggregate, hour_bucket

A = UUID("00000000-0000-0000-0000-00000000000a")
B = UUID("00000000-0000-0000-0000-00000000000b")


def click(link, ts, host="(direct)"):
    return ResolvedClick(link_id=link, occurred_at=ts, referrer_host=host)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_hour_bucket_truncates_in_utc():
    eastern = timezone(timedelta(hours=-4))
    assert hour_bucket(datetime(2026, 9, 30, 23, 59, 59, tzinfo=eastern)) == utc(2026, 10, 1, 3)


def test_counts_group_by_link_and_hour():
    deltas = aggregate(
        [
            click(A, utc(2026, 10, 1, 12, 5)),
            click(A, utc(2026, 10, 1, 12, 55)),
            click(A, utc(2026, 10, 1, 13, 0)),
            click(B, utc(2026, 10, 1, 12, 30)),
        ]
    )
    assert deltas.hourly == {
        (A, utc(2026, 10, 1, 12)): 2,
        (A, utc(2026, 10, 1, 13)): 1,
        (B, utc(2026, 10, 1, 12)): 1,
    }


def test_referrers_group_by_link_utc_date_and_host():
    eastern = timezone(timedelta(hours=-4))
    deltas = aggregate(
        [
            click(A, datetime(2026, 9, 30, 21, 0, tzinfo=eastern), "news.example"),  # 01:00 UTC Oct 1
            click(A, utc(2026, 10, 1, 9), "news.example"),
            click(A, utc(2026, 9, 30, 9), "news.example"),
            click(A, utc(2026, 10, 1, 9)),
        ]
    )
    assert deltas.referrers == {
        (A, date(2026, 10, 1), "news.example"): 2,
        (A, date(2026, 9, 30), "news.example"): 1,
        (A, date(2026, 10, 1), "(direct)"): 1,
    }


def test_empty_input_is_empty():
    deltas = aggregate([])
    assert deltas.is_empty
    assert deltas.link_ids == frozenset()


def test_without_drops_links_from_both_tables():
    deltas = aggregate([click(A, utc(2026, 10, 1, 12)), click(B, utc(2026, 10, 1, 12), "x.example")])
    assert deltas.link_ids == {A, B}
    kept = deltas.without({B})
    assert kept.link_ids == {A}
    assert all(key[0] == A for key in kept.hourly)
    assert all(key[0] == A for key in kept.referrers)
    assert not kept.is_empty
```

Run: `uv run pytest processor/tests/unit/test_aggregate.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 7: Implement `aggregate.py`**

`processor/src/shortener_processor/aggregate.py`:
```python
"""Click events → rollup deltas (spec §5.3 step 3). Pure: no framework imports."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

HourKey = tuple[UUID, datetime]
ReferrerKey = tuple[UUID, date, str]


@dataclass(frozen=True)
class ResolvedClick:
    link_id: UUID
    occurred_at: datetime
    referrer_host: str


@dataclass(frozen=True)
class RollupDeltas:
    hourly: dict[HourKey, int] = field(default_factory=dict)
    referrers: dict[ReferrerKey, int] = field(default_factory=dict)

    @property
    def link_ids(self) -> frozenset[UUID]:
        return frozenset(key[0] for key in self.hourly) | frozenset(
            key[0] for key in self.referrers
        )

    @property
    def is_empty(self) -> bool:
        return not self.hourly and not self.referrers

    def without(self, link_ids: set[UUID] | frozenset[UUID]) -> "RollupDeltas":
        return RollupDeltas(
            hourly={k: v for k, v in self.hourly.items() if k[0] not in link_ids},
            referrers={k: v for k, v in self.referrers.items() if k[0] not in link_ids},
        )


def hour_bucket(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def aggregate(clicks: Iterable[ResolvedClick]) -> RollupDeltas:
    hourly: Counter[HourKey] = Counter()
    referrers: Counter[ReferrerKey] = Counter()
    for click in clicks:
        hour = hour_bucket(click.occurred_at)
        hourly[(click.link_id, hour)] += 1
        referrers[(click.link_id, hour.date(), click.referrer_host)] += 1
    return RollupDeltas(hourly=dict(hourly), referrers=dict(referrers))
```

Run: `uv run pytest processor/tests/unit -q`
Expected: all PASS.

- [ ] **Step 8: Gates and commit**

```bash
uv run ruff format . && make check && git add processor pyproject.toml uv.lock Makefile api/Dockerfile tools/keycloak-tools/Dockerfile && git commit -m "feat(processor): package, settings, referrer normalization, and click aggregation

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```
Expected: `make check` passes, and `referrers.py` and `aggregate.py` are at 100% branch coverage.

---

### Task 3: Rollup store and link resolver (Postgres)

**Files:**
- Create: `processor/src/shortener_processor/{db,rollup_store,link_resolver}.py`
- Create: `processor/tests/integration/conftest.py`
- Test: `processor/tests/integration/test_rollup_store.py`, `processor/tests/integration/test_link_resolver.py`

**Interfaces:**
- Consumes: `RollupDeltas` (Task 2); the shared fixtures (Task 1).
- Produces:
  - `db`: `metadata`, `links` (only `id` and `code`), `link_clicks_hourly`, `link_referrers_daily`, `pipeline_status`
  - `CommitResult(committed_links: frozenset[UUID], skipped_links: frozenset[UUID])`
  - `RollupStore` (Protocol): `async commit(deltas, now: datetime) -> CommitResult`
  - `PostgresRollupStore(engine)`
  - `LinkResolver` (Protocol): `async resolve(codes: set[str]) -> dict[str, UUID]` (unknown codes are absent)
  - `PostgresLinkResolver(engine, *, ttl_seconds=60.0, monotonic=time.monotonic)`. It caches positive hits for `ttl_seconds` and never caches misses.
  - Integration fixture `processor_engine` (an `AsyncEngine` as `processor_user`)

`PostgresRollupStore.commit`, in **one transaction**:
1. `SELECT id FROM links WHERE id IN (...)`. Links that are missing go into `skipped_links`.
2. Upsert the remaining hourly rows, then the remaining referrer rows, each sorted by primary key, with `count = count + excluded.count`.
3. `UPDATE analytics.pipeline_status SET last_committed_at = now WHERE id = 1`.

A link deleted after step 1 makes the transaction raise a foreign-key violation. The caller treats that like any commit failure (deviation 1).

- [ ] **Step 1: Write the failing store and resolver tests**

`processor/tests/integration/conftest.py`:
```python
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shortener_testing.fixtures import PgServer


@pytest.fixture(autouse=True)
def _reset_database(reset_database: None) -> None:
    """Every processor integration test starts from the post-migration state."""


@pytest.fixture
async def processor_engine(migrated: PgServer) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated.url("processor_user"))
    yield engine
    await engine.dispose()
```

`processor/tests/integration/test_rollup_store.py`:
```python
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from shortener_processor.aggregate import RollupDeltas
from shortener_processor.rollup_store import PostgresRollupStore

pytestmark = pytest.mark.integration

H12 = datetime(2026, 10, 1, 12, tzinfo=UTC)
NOW = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)


def rows(migrated, sql):
    with migrated.connect("migrator") as conn:
        return conn.execute(sql).fetchall()


async def test_commit_upserts_and_accumulates(processor_engine, insert_link, migrated):
    link = insert_link()
    store = PostgresRollupStore(processor_engine)
    deltas = RollupDeltas(
        hourly={(link, H12): 2}, referrers={(link, date(2026, 10, 1), "news.example"): 2}
    )
    first = await store.commit(deltas, NOW)
    await store.commit(deltas, NOW)
    assert first.committed_links == {link}
    assert first.skipped_links == frozenset()
    assert rows(migrated, "SELECT count FROM analytics.link_clicks_hourly") == [(4,)]
    assert rows(migrated, "SELECT referrer_host, count FROM analytics.link_referrers_daily") == [
        ("news.example", 4)
    ]


async def test_commit_records_pipeline_status(processor_engine, insert_link, migrated):
    link = insert_link()
    await PostgresRollupStore(processor_engine).commit(RollupDeltas(hourly={(link, H12): 1}), NOW)
    assert rows(migrated, "SELECT last_committed_at FROM analytics.pipeline_status") == [(NOW,)]


async def test_commit_skips_links_that_no_longer_exist(processor_engine, insert_link, migrated):
    kept, gone = insert_link(), uuid4()
    result = await PostgresRollupStore(processor_engine).commit(
        RollupDeltas(hourly={(kept, H12): 1, (gone, H12): 5}), NOW
    )
    assert result.committed_links == {kept}
    assert result.skipped_links == {gone}
    assert rows(migrated, "SELECT link_id, count FROM analytics.link_clicks_hourly") == [(kept, 1)]


async def test_commit_with_only_unknown_links_still_succeeds(processor_engine, migrated):
    gone = uuid4()
    result = await PostgresRollupStore(processor_engine).commit(
        RollupDeltas(hourly={(gone, H12): 1}), NOW
    )
    assert result.skipped_links == {gone}
    assert rows(migrated, "SELECT count(*) FROM analytics.link_clicks_hourly") == [(0,)]
```

`processor/tests/integration/test_link_resolver.py`:
```python
import pytest

from shortener_processor.link_resolver import PostgresLinkResolver

pytestmark = pytest.mark.integration


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_resolves_known_codes_and_omits_unknown(processor_engine, insert_link):
    link = insert_link(code="aZ3kQ9x")
    resolved = await PostgresLinkResolver(processor_engine).resolve({"aZ3kQ9x", "nope123"})
    assert resolved == {"aZ3kQ9x": link}


async def test_codes_are_case_sensitive(processor_engine, insert_link):
    insert_link(code="aZ3kQ9x")
    assert await PostgresLinkResolver(processor_engine).resolve({"AZ3KQ9X"}) == {}


async def test_hits_are_cached_until_ttl_expires(processor_engine, insert_link, migrated):
    link = insert_link(code="cache01")
    clock = FakeMonotonic()
    resolver = PostgresLinkResolver(processor_engine, ttl_seconds=60, monotonic=clock)
    assert await resolver.resolve({"cache01"}) == {"cache01": link}
    with migrated.connect("migrator") as conn:
        conn.execute("DELETE FROM public.links WHERE id = %s", (link,))
    assert await resolver.resolve({"cache01"}) == {"cache01": link}  # served from cache
    clock.now += 61
    assert await resolver.resolve({"cache01"}) == {}


async def test_misses_are_not_cached(processor_engine, insert_link):
    resolver = PostgresLinkResolver(processor_engine)
    assert await resolver.resolve({"later01"}) == {}
    link = insert_link(code="later01")
    assert await resolver.resolve({"later01"}) == {"later01": link}


async def test_empty_input_does_not_query(processor_engine):
    assert await PostgresLinkResolver(processor_engine).resolve(set()) == {}
```

Run (colima env exported): `uv run pytest processor/tests/integration -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_processor.rollup_store'`.

- [ ] **Step 2: Implement the tables, store, and resolver**

`processor/src/shortener_processor/db.py`:
```python
"""The tables the processor touches, mirroring api/alembic (migrations own the DDL).
processor_user may read only links.id and links.code."""

import sqlalchemy as sa

metadata = sa.MetaData()
TZ = sa.DateTime(timezone=True)

links = sa.Table(
    "links",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("code", sa.String(32), nullable=False),
    schema="public",
)

link_clicks_hourly = sa.Table(
    "link_clicks_hourly",
    metadata,
    sa.Column("link_id", sa.Uuid, primary_key=True),
    sa.Column("bucket_start", TZ, primary_key=True),
    sa.Column("count", sa.BigInteger, nullable=False),
    schema="analytics",
)

link_referrers_daily = sa.Table(
    "link_referrers_daily",
    metadata,
    sa.Column("link_id", sa.Uuid, primary_key=True),
    sa.Column("bucket_date", sa.Date, primary_key=True),
    sa.Column("referrer_host", sa.Text, primary_key=True),
    sa.Column("count", sa.BigInteger, nullable=False),
    schema="analytics",
)

pipeline_status = sa.Table(
    "pipeline_status",
    metadata,
    sa.Column("id", sa.SmallInteger, primary_key=True),
    sa.Column("last_committed_at", TZ),
    schema="analytics",
)
```

`processor/src/shortener_processor/rollup_store.py`:
```python
"""Rollup persistence (spec §5.3 step 4): one transaction per batch, additive upserts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from shortener_processor.aggregate import RollupDeltas
from shortener_processor.db import link_clicks_hourly, link_referrers_daily, links, pipeline_status


@dataclass(frozen=True)
class CommitResult:
    committed_links: frozenset[UUID]
    skipped_links: frozenset[UUID]


class RollupStore(Protocol):
    async def commit(self, deltas: RollupDeltas, now: datetime) -> CommitResult: ...


async def _upsert(conn: AsyncConnection, table: sa.Table, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    stmt = pg_insert(table).values(rows)
    keys = [column for column in table.primary_key.columns]
    stmt = stmt.on_conflict_do_update(
        index_elements=keys, set_={"count": table.c["count"] + stmt.excluded["count"]}
    )
    await conn.execute(stmt)


class PostgresRollupStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def commit(self, deltas: RollupDeltas, now: datetime) -> CommitResult:
        wanted = deltas.link_ids
        async with self._engine.begin() as conn:
            existing: frozenset[UUID] = frozenset()
            if wanted:
                result = await conn.scalars(sa.select(links.c.id).where(links.c.id.in_(wanted)))
                existing = frozenset(result)
            skipped = wanted - existing
            kept = deltas.without(skipped)
            # Sorted by primary key so concurrent processors lock rows in the same order.
            await _upsert(
                conn,
                link_clicks_hourly,
                [
                    {"link_id": link_id, "bucket_start": hour, "count": count}
                    for (link_id, hour), count in sorted(kept.hourly.items())
                ],
            )
            await _upsert(
                conn,
                link_referrers_daily,
                [
                    {"link_id": link_id, "bucket_date": day, "referrer_host": host, "count": count}
                    for (link_id, day, host), count in sorted(kept.referrers.items())
                ],
            )
            await conn.execute(
                sa.update(pipeline_status)
                .where(pipeline_status.c.id == 1)
                .values(last_committed_at=now)
            )
        return CommitResult(committed_links=existing, skipped_links=frozenset(skipped))
```
`sorted()` over `(UUID, datetime)` and `(UUID, date, str)` tuples works, because each element type is ordered.

`processor/src/shortener_processor/link_resolver.py`:
```python
"""code → link_id for events that arrive without link_id (spec §5.3 step 2)."""

import time
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from shortener_processor.db import links


class LinkResolver(Protocol):
    async def resolve(self, codes: set[str]) -> dict[str, UUID]: ...


class PostgresLinkResolver:
    """Caches hits for `ttl_seconds`; misses are never cached (the link may be created soon)."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._ttl = ttl_seconds
        self._monotonic = monotonic
        self._cache: dict[str, tuple[UUID, float]] = {}

    async def resolve(self, codes: set[str]) -> dict[str, UUID]:
        now = self._monotonic()
        found = {
            code: entry[0]
            for code in codes
            if (entry := self._cache.get(code)) is not None and entry[1] > now
        }
        missing = codes - found.keys()
        if missing:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    sa.select(links.c.code, links.c.id).where(links.c.code.in_(missing))
                )
                for code, link_id in result.all():
                    found[code] = link_id
                    self._cache[code] = (link_id, now + self._ttl)
        return found
```

Run: `uv run pytest processor/tests/integration -q`
Expected: all PASS.

- [ ] **Step 3: Gates and commit**

```bash
uv run ruff format . && make check && git add processor && git commit -m "feat(processor): Postgres rollup store and cached link resolver

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 4: Batch processor and processor metrics

**Files:**
- Create: `processor/src/shortener_processor/{queue,telemetry,batch}.py`
- Create: `processor/tests/conftest.py` (metrics fixtures)
- Test: `processor/tests/unit/test_telemetry.py`, `processor/tests/unit/test_batch.py`

**Interfaces:**
- Consumes:
  - `decode`, `SqsMessage`, `InvalidEventError`, and `ClickEvent` (Plan 1)
  - `referrer_host` and `aggregate`/`ResolvedClick` (Task 2)
  - the `RollupStore`/`CommitResult` and `LinkResolver` protocols (Task 3)
- Produces:
  - `queue.ReceivedMessage(message_id: str, receipt_handle: str, body: str, attributes: dict[str, str])`
  - `queue.QueueClient` (Protocol) with:
    - `async receive(max_messages: int, wait_seconds: int) -> list[ReceivedMessage]`
    - `async delete(receipt_handles: Sequence[str]) -> list[str]` (returns the handles that failed)
    - `async depth(queue_name: str) -> int`
  - `ProcessorTelemetry(meter)` with `messages` (counter `shortener.processor.messages`), `batch_duration` (histogram, s), `event_lag` (histogram, s), and `set_queue_depth(queue: str, depth: int)`, which feeds the `shortener.queue.depth{queue}` observable gauge
  - `BatchOutcome(ok: int, invalid: int, unknown_link: int, deleted: int, delete_failures: int)`
  - `BatchProcessor(queue, resolver, store, telemetry, *, clock: Callable[[], datetime] = utc_now, perf_counter: Callable[[], float] = time.perf_counter)` with `async process(messages: Sequence[ReceivedMessage]) -> BatchOutcome`

`process()` contract:
1. **Decode each message on its own.** On `InvalidEventError`, count it as `invalid`, log a warning with the `message_id`, and **leave it on the queue**.
2. **Drop duplicate `event_id`s within the batch.** The first copy counts; later copies are deleted silently, with no metric.
3. **Resolve `link_id`** for events without one, with **one** `resolver.resolve(codes)` call. If a code is unknown, that event becomes `unknown_link`.
4. **Commit** `aggregate(...)`, with `referrer_host` applied, through `store.commit(deltas, now)`. Skip the call when the deltas are empty. If the commit raises, the exception propagates and **nothing is deleted**.
5. **Remap skipped links.** Events whose link is in `skipped_links` become `unknown_link`.
6. **Record metrics:** `messages{result}` counts, plus `event_lag = max(0, now - occurred_at)` for each `ok` event.
7. **Delete** the `ok`, `unknown_link`, and duplicate messages in one `queue.delete` call. Failed deletes are logged; they cause the D13 overcount on redelivery.
8. **Record `batch_duration`** with `perf_counter`.

- [ ] **Step 1: Write the failing telemetry test**

`processor/tests/conftest.py`:
```python
from collections.abc import Callable
from typing import Any

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def meter(metric_reader: InMemoryMetricReader) -> Any:
    return MeterProvider(metric_readers=[metric_reader]).get_meter("test")


@pytest.fixture
def metric_points(metric_reader: InMemoryMetricReader) -> Callable[[str], list[Any]]:
    def _points(name: str) -> list[Any]:
        data = metric_reader.get_metrics_data()
        if data is None:
            return []
        return [
            point
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == name
            for point in metric.data.data_points
        ]

    return _points


@pytest.fixture
def metric_value(metric_points: Callable[[str], list[Any]]) -> Callable[..., float]:
    """Counter sum / histogram count / gauge value for points whose attributes match exactly."""

    def _value(name: str, attributes: dict[str, str] | None = None) -> float:
        total = 0.0
        for point in metric_points(name):
            if attributes is not None and dict(point.attributes or {}) != attributes:
                continue
            total += getattr(point, "value", None) or getattr(point, "count", 0)
        return total

    return _value
```

`processor/tests/unit/test_telemetry.py`:
```python
from shortener_processor.telemetry import ProcessorTelemetry


def test_instruments_use_spec_names(meter, metric_value):
    telemetry = ProcessorTelemetry(meter)
    telemetry.messages.add(3, {"result": "ok"})
    telemetry.messages.add(1, {"result": "invalid"})
    telemetry.batch_duration.record(0.02)
    telemetry.event_lag.record(1.5)
    telemetry.set_queue_depth("click-events", 7)
    telemetry.set_queue_depth("click-events-dlq", 1)
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 3
    assert metric_value("shortener.processor.messages", {"result": "invalid"}) == 1
    assert metric_value("shortener.processor.batch.duration") == 1
    assert metric_value("shortener.processor.event_lag") == 1
    assert metric_value("shortener.queue.depth", {"queue": "click-events"}) == 7
    assert metric_value("shortener.queue.depth", {"queue": "click-events-dlq"}) == 1


def test_queue_depth_is_silent_until_first_poll(meter, metric_points):
    ProcessorTelemetry(meter)
    assert metric_points("shortener.queue.depth") == []
```

Run: `uv run pytest processor/tests/unit/test_telemetry.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Implement `queue.py` and `telemetry.py`**

`processor/src/shortener_processor/queue.py`:
```python
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ReceivedMessage:
    message_id: str
    receipt_handle: str
    body: str
    attributes: dict[str, str] = field(default_factory=dict)


class QueueClient(Protocol):
    async def receive(self, max_messages: int, wait_seconds: int) -> list[ReceivedMessage]: ...

    async def delete(self, receipt_handles: Sequence[str]) -> list[str]:
        """Delete messages; return the receipt handles that could NOT be deleted."""
        ...

    async def depth(self, queue_name: str) -> int: ...
```

`processor/src/shortener_processor/telemetry.py`:
```python
"""Processor metrics (spec §10). No per-link attributes."""

from collections.abc import Iterable

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import CallbackOptions, Meter, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from shortener_processor.settings import ProcessorSettings


class ProcessorTelemetry:
    def __init__(self, meter: Meter) -> None:
        self.messages = meter.create_counter(
            "shortener.processor.messages", description="Messages handled by result"
        )
        self.batch_duration = meter.create_histogram("shortener.processor.batch.duration", unit="s")
        self.event_lag = meter.create_histogram(
            "shortener.processor.event_lag", unit="s", description="occurred_at → commit"
        )
        self._depths: dict[str, int] = {}
        meter.create_observable_gauge("shortener.queue.depth", callbacks=[self._observe_depths])

    def set_queue_depth(self, queue: str, depth: int) -> None:
        self._depths[queue] = depth

    def _observe_depths(self, _: CallbackOptions) -> Iterable[Observation]:
        for queue, depth in self._depths.items():
            yield Observation(depth, {"queue": queue})


def configure_meter_provider(settings: ProcessorSettings) -> MeterProvider:
    resource = Resource.create(
        {
            "service.name": "shortener-click-processor",
            "service.version": settings.service_version,
            "deployment.environment": settings.deployment_environment,
        }
    )
    readers: list[MetricReader] = []
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPMetricExporter(
            endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/metrics"
        )
        readers.append(PeriodicExportingMetricReader(exporter, export_interval_millis=10_000))
    return MeterProvider(resource=resource, metric_readers=readers)
```
`configure_meter_provider` copies `shortener_api.telemetry.configure_meter_provider` with a different `service.name`. That duplication is accepted until Plan 5 consolidates telemetry setup.

Run: `uv run pytest processor/tests/unit/test_telemetry.py -q`
Expected: PASS.

- [ ] **Step 3: Write the failing batch-processor tests (hand-written fakes)**

`processor/tests/unit/test_batch.py`:
```python
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from shortener_events import ClickEvent, encode
from shortener_processor.batch import BatchProcessor
from shortener_processor.queue import ReceivedMessage
from shortener_processor.rollup_store import CommitResult
from shortener_processor.telemetry import ProcessorTelemetry

A = UUID("00000000-0000-0000-0000-00000000000a")
B = UUID("00000000-0000-0000-0000-00000000000b")
NOW = datetime(2026, 10, 1, 12, 0, 10, tzinfo=UTC)


def message(n: int, *, code="aaaaaaa", link_id=A, referrer=None, event_id=None, seconds_ago=10):
    event = ClickEvent(
        event_id=event_id or f"e{n}",
        occurred_at=NOW - timedelta(seconds=seconds_ago),
        source="api",
        code=code,
        link_id=link_id,
        referrer=referrer,
    )
    encoded = encode(event)
    return ReceivedMessage(f"m{n}", f"r{n}", encoded.body, encoded.attributes)


def poison(n: int) -> ReceivedMessage:
    return ReceivedMessage(f"m{n}", f"r{n}", "{not json", {"type": "link.clicked", "version": "1"})


class FakeQueue:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.deleted: list[str] = []
        self._failing = failing or set()

    async def receive(self, max_messages, wait_seconds):
        return []

    async def delete(self, receipt_handles):
        self.deleted.extend(h for h in receipt_handles if h not in self._failing)
        return [h for h in receipt_handles if h in self._failing]

    async def depth(self, queue_name):
        return 0


class FakeResolver:
    def __init__(self, known: dict[str, UUID]) -> None:
        self.known = known
        self.calls: list[set[str]] = []

    async def resolve(self, codes):
        self.calls.append(set(codes))
        return {c: self.known[c] for c in codes if c in self.known}


class FakeStore:
    def __init__(self, skipped: frozenset[UUID] = frozenset(), error: Exception | None = None):
        self.commits = []
        self._skipped = skipped
        self._error = error

    async def commit(self, deltas, now):
        if self._error is not None:
            raise self._error
        self.commits.append((deltas, now))
        return CommitResult(deltas.link_ids - self._skipped, deltas.link_ids & self._skipped)


@pytest.fixture
def make(meter):
    def _make(queue=None, resolver=None, store=None):
        queue = queue or FakeQueue()
        resolver = resolver or FakeResolver({})
        store = store or FakeStore()
        processor = BatchProcessor(
            queue, resolver, store, ProcessorTelemetry(meter), clock=lambda: NOW,
            perf_counter=iter([0.0, 0.25]).__next__,
        )  # fmt: skip
        return processor, queue, resolver, store

    return _make


async def test_valid_batch_commits_then_deletes(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process([message(1), message(2, link_id=B, referrer="https://n.example/x")])
    [(deltas, now)] = store.commits
    assert now == NOW
    assert deltas.hourly == {(A, datetime(2026, 10, 1, 12, tzinfo=UTC)): 1, (B, datetime(2026, 10, 1, 12, tzinfo=UTC)): 1}
    assert deltas.referrers[(B, date(2026, 10, 1), "n.example")] == 1
    assert deltas.referrers[(A, date(2026, 10, 1), "(direct)")] == 1
    assert queue.deleted == ["r1", "r2"]
    assert (outcome.ok, outcome.invalid, outcome.unknown_link, outcome.deleted) == (2, 0, 0, 2)
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 2
    assert metric_value("shortener.processor.event_lag") == 2
    assert metric_value("shortener.processor.batch.duration") == 1


async def test_invalid_message_is_counted_and_left_on_the_queue(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process([message(1), poison(2)])
    assert queue.deleted == ["r1"]
    assert outcome.invalid == 1
    assert len(store.commits) == 1
    assert metric_value("shortener.processor.messages", {"result": "invalid"}) == 1


async def test_missing_link_id_is_resolved_once_per_batch(make):
    resolver = FakeResolver({"aaaaaaa": A})
    processor, queue, resolver, store = make(resolver=resolver)
    await processor.process([message(1, link_id=None), message(2, link_id=None), message(3)])
    assert resolver.calls == [{"aaaaaaa"}]
    assert store.commits[0][0].hourly == {(A, datetime(2026, 10, 1, 12, tzinfo=UTC)): 3}


async def test_unresolvable_code_is_unknown_link_and_deleted(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process([message(1, code="gone000", link_id=None)])
    assert store.commits == []  # nothing to commit
    assert queue.deleted == ["r1"]
    assert outcome.unknown_link == 1
    assert metric_value("shortener.processor.messages", {"result": "unknown_link"}) == 1


async def test_store_skipped_links_count_as_unknown_and_are_deleted(make, metric_value):
    processor, queue, _, _ = make(store=FakeStore(skipped=frozenset({B})))
    outcome = await processor.process([message(1), message(2, link_id=B)])
    assert (outcome.ok, outcome.unknown_link) == (1, 1)
    assert sorted(queue.deleted) == ["r1", "r2"]
    assert metric_value("shortener.processor.messages", {"result": "unknown_link"}) == 1


async def test_commit_failure_deletes_nothing(make):
    processor, queue, _, _ = make(store=FakeStore(error=ConnectionError("db down")))
    with pytest.raises(ConnectionError):
        await processor.process([message(1), message(2)])
    assert queue.deleted == []


async def test_duplicate_event_ids_count_once_and_all_copies_are_deleted(make, metric_value):
    processor, queue, _, store = make()
    outcome = await processor.process([message(1, event_id="dup"), message(2, event_id="dup")])
    assert store.commits[0][0].hourly == {(A, datetime(2026, 10, 1, 12, tzinfo=UTC)): 1}
    assert sorted(queue.deleted) == ["r1", "r2"]
    assert outcome.ok == 1
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 1


async def test_delete_failures_are_reported_not_raised(make):
    processor, _, _, _ = make(queue=FakeQueue(failing={"r2"}))
    outcome = await processor.process([message(1), message(2)])
    assert (outcome.deleted, outcome.delete_failures) == (1, 1)


async def test_event_lag_is_never_negative(make, metric_points):
    processor, _, _, _ = make()
    await processor.process([message(1, seconds_ago=-30)])  # producer clock ahead of ours
    [point] = metric_points("shortener.processor.event_lag")
    assert point.sum == 0


async def test_empty_batch_is_a_no_op(make):
    processor, queue, resolver, store = make()
    outcome = await processor.process([])
    assert (store.commits, queue.deleted, resolver.calls) == ([], [], [])
    assert outcome.ok == 0
```

Run: `uv run pytest processor/tests/unit/test_batch.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_processor.batch'`.

- [ ] **Step 4: Implement `batch.py`**

`processor/src/shortener_processor/batch.py`:
```python
"""One batch: decode → resolve → aggregate → commit → delete (spec §5.3)."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from shortener_events import ClickEvent, InvalidEventError, SqsMessage, decode
from shortener_processor.aggregate import ResolvedClick, aggregate
from shortener_processor.link_resolver import LinkResolver
from shortener_processor.queue import QueueClient, ReceivedMessage
from shortener_processor.referrers import referrer_host
from shortener_processor.rollup_store import CommitResult, RollupStore
from shortener_processor.telemetry import ProcessorTelemetry

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class BatchOutcome:
    ok: int
    invalid: int
    unknown_link: int
    deleted: int
    delete_failures: int


class BatchProcessor:
    def __init__(
        self,
        queue: QueueClient,
        resolver: LinkResolver,
        store: RollupStore,
        telemetry: ProcessorTelemetry,
        *,
        clock: Callable[[], datetime] = utc_now,
        perf_counter: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._queue = queue
        self._resolver = resolver
        self._store = store
        self._telemetry = telemetry
        self._clock = clock
        self._perf_counter = perf_counter

    def _decode(
        self, messages: Sequence[ReceivedMessage]
    ) -> tuple[list[tuple[ReceivedMessage, ClickEvent]], list[ReceivedMessage], int]:
        """Valid (message, event) pairs, duplicate copies to delete, and the invalid count."""
        valid: list[tuple[ReceivedMessage, ClickEvent]] = []
        duplicates: list[ReceivedMessage] = []
        seen: set[str] = set()
        invalid = 0
        for message in messages:
            try:
                event = decode(SqsMessage(message.body, message.attributes))
            except InvalidEventError as exc:
                invalid += 1
                logger.warning("invalid click message %s left for redrive: %s", message.message_id, exc)
                continue
            if event.event_id in seen:
                duplicates.append(message)
                continue
            seen.add(event.event_id)
            valid.append((message, event))
        return valid, duplicates, invalid

    async def process(self, messages: Sequence[ReceivedMessage]) -> BatchOutcome:
        if not messages:
            return BatchOutcome(0, 0, 0, 0, 0)
        started = self._perf_counter()
        valid, duplicates, invalid = self._decode(messages)

        codes = {event.code for _, event in valid if event.link_id is None}
        resolved = await self._resolver.resolve(codes) if codes else {}
        located: list[tuple[ReceivedMessage, ClickEvent, UUID]] = []
        unknown: list[ReceivedMessage] = []
        for message, event in valid:
            link_id = event.link_id or resolved.get(event.code)
            if link_id is None:
                unknown.append(message)
            else:
                located.append((message, event, link_id))

        deltas = aggregate(
            ResolvedClick(link_id, event.occurred_at, referrer_host(event.referrer))
            for _, event, link_id in located
        )
        now = self._clock()
        result = (
            CommitResult(frozenset(), frozenset())
            if deltas.is_empty
            else await self._store.commit(deltas, now)  # raises → nothing below runs
        )
        committed = [(m, e) for m, e, link_id in located if link_id not in result.skipped_links]
        unknown += [m for m, _, link_id in located if link_id in result.skipped_links]

        self._telemetry.messages.add(len(committed), {"result": "ok"})
        self._telemetry.messages.add(invalid, {"result": "invalid"})
        self._telemetry.messages.add(len(unknown), {"result": "unknown_link"})
        for _, event in committed:
            self._telemetry.event_lag.record(max(0.0, (now - event.occurred_at).total_seconds()))

        handles = [m.receipt_handle for m, _ in committed]
        handles += [m.receipt_handle for m in unknown + duplicates]
        failed = await self._queue.delete(handles) if handles else []
        if failed:
            logger.warning("%d processed messages could not be deleted; they will be recounted", len(failed))
        self._telemetry.batch_duration.record(self._perf_counter() - started)
        return BatchOutcome(
            ok=len(committed),
            invalid=invalid,
            unknown_link=len(unknown),
            deleted=len(handles) - len(failed),
            delete_failures=len(failed),
        )
```

Run: `uv run pytest processor/tests/unit -q`
Expected: all PASS.

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff format . && make check && git add processor && git commit -m "feat(processor): batch processing with commit-then-delete and processor metrics

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 5: SQS queue client, consumer loop, and queue-level integration

**Files:**
- Create: `processor/src/shortener_processor/{sqs,consumer}.py`
- Test: `processor/tests/unit/test_consumer.py`, `processor/tests/integration/test_sqs.py`

**Interfaces:**
- Consumes:
  - `QueueClient`/`ReceivedMessage` (Task 4)
  - `BatchProcessor`/`BatchOutcome` (Task 4)
  - `from_sqs_attributes` (Plan 1)
  - the `elasticmq` and `sqs_client` fixtures (Task 1)
- Produces:
  - `sqs.SQS_CONFIG = botocore.config.Config(connect_timeout=2, read_timeout=25, retries={"total_max_attempts": 3, "mode": "standard"})`. `read_timeout` must exceed the 20 s long poll.
  - `SqsQueueClient(client, queue_name)`, implementing `QueueClient`. It resolves and caches queue URLs and deletes in chunks of 10.
  - `consumer.Processor` (Protocol): `async process(messages) -> BatchOutcome`
  - `Consumer(queue, processor, *, max_messages=100, window_seconds=1.0, wait_seconds=20, error_backoff_seconds=1.0, monotonic=time.monotonic, sleep=asyncio.sleep)` with:
    - `async collect() -> list[ReceivedMessage]`
    - `async run_once() -> BatchOutcome | None`
    - `async run(stop: asyncio.Event) -> None`
    - `seconds_since_heartbeat() -> float`

Collection rule:
- The first receive of a batch long-polls `wait_seconds`. An empty result returns `[]`, so `run()` can check `stop`.
- Once at least one message is in hand, the window ends `window_seconds` after the first message arrived. Each follow-up receive uses `WaitTimeSeconds=1`.
- Collection stops at `max_messages`.
- Each `receive` asks for `min(10, remaining)`.
- The heartbeat is updated after every receive.

`run()`:
- Loops until `stop` is set.
- If a batch raises (a commit failure, the DB down), it logs with `exc_info`, sleeps `error_backoff_seconds`, and continues. Messages that weren't deleted come back after the visibility timeout.
- `CancelledError` propagates.

- [ ] **Step 1: Write the failing consumer tests**

`processor/tests/unit/test_consumer.py`:
```python
import asyncio

import pytest

from shortener_processor.batch import BatchOutcome
from shortener_processor.consumer import Consumer
from shortener_processor.queue import ReceivedMessage


def msgs(start: int, count: int) -> list[ReceivedMessage]:
    return [ReceivedMessage(f"m{i}", f"r{i}", "{}") for i in range(start, start + count)]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedQueue:
    """Each receive returns the next scripted list and advances the fake clock by `step`."""

    def __init__(self, script, clock: FakeClock, step: float = 0.3) -> None:
        self.script = list(script)
        self.calls: list[tuple[int, int]] = []
        self._clock = clock
        self._step = step

    async def receive(self, max_messages, wait_seconds):
        self.calls.append((max_messages, wait_seconds))
        self._clock.now += self._step
        await asyncio.sleep(0)
        return self.script.pop(0) if self.script else []

    async def delete(self, receipt_handles):
        return []

    async def depth(self, queue_name):
        return 0


class RecordingProcessor:
    def __init__(self, error: Exception | None = None) -> None:
        self.batches: list[list[ReceivedMessage]] = []
        self.error = error

    async def process(self, messages):
        self.batches.append(list(messages))
        if self.error is not None:
            error, self.error = self.error, None  # fail once
            raise error
        return BatchOutcome(len(messages), 0, 0, len(messages), 0)


async def test_empty_first_poll_returns_nothing_after_a_long_poll():
    clock = FakeClock()
    queue = ScriptedQueue([[]], clock)
    consumer = Consumer(queue, RecordingProcessor(), monotonic=clock)
    assert await consumer.collect() == []
    assert queue.calls == [(10, 20)]


async def test_collects_until_the_window_closes():
    clock = FakeClock()
    script = [msgs(n * 10, 10) for n in range(8)]
    queue = ScriptedQueue(script, clock)
    batch = await Consumer(queue, RecordingProcessor(), monotonic=clock).collect()
    # Each receive advances the clock 0.3 s. The first batch arrives at t=0.3, so the window
    # closes at 1.3. The window is checked before each receive: receives start at
    # 0, 0.3, 0.6, 0.9 and 1.2 (all < 1.3), so five receives = 50 messages, then stop at t=1.5.
    assert len(batch) == 50
    assert len(queue.calls) == 5
    assert queue.calls[0] == (10, 20)
    assert all(wait == 1 for _, wait in queue.calls[1:])


async def test_stops_at_max_messages_and_asks_only_for_what_fits():
    clock = FakeClock()
    queue = ScriptedQueue([msgs(0, 10), msgs(10, 10), msgs(20, 5)], clock, step=0.0)
    batch = await Consumer(queue, RecordingProcessor(), max_messages=25, monotonic=clock).collect()
    assert len(batch) == 25
    assert [n for n, _ in queue.calls] == [10, 10, 5]


async def test_run_once_hands_the_batch_to_the_processor():
    clock = FakeClock()
    processor = RecordingProcessor()
    consumer = Consumer(ScriptedQueue([msgs(0, 3)], clock), processor, monotonic=clock)
    outcome = await consumer.run_once()
    assert outcome is not None and outcome.ok == 3
    assert len(processor.batches[0]) == 3


async def test_run_survives_a_failing_batch():
    clock = FakeClock()
    slept: list[float] = []
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    processor = RecordingProcessor(error=ConnectionError("db down"))
    # step=2.0 > window: each batch closes after one follow-up receive, so the two scripted
    # messages land in two separate batches (an empty script returns []).
    queue = ScriptedQueue([msgs(0, 1), [], msgs(1, 1)], clock, step=2.0)
    consumer = Consumer(queue, processor, monotonic=clock, sleep=sleep, error_backoff_seconds=1.0)

    async def stop_when_second_batch_processed() -> None:
        while len(processor.batches) < 2:
            await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(consumer.run(stop), stop_when_second_batch_processed())
    assert len(processor.batches) == 2  # the loop kept going after the failure
    assert slept == [1.0]


async def test_heartbeat_tracks_the_last_receive():
    clock = FakeClock()
    consumer = Consumer(ScriptedQueue([[]], clock), RecordingProcessor(), monotonic=clock)
    await consumer.collect()
    clock.now += 5
    assert consumer.seconds_since_heartbeat() == pytest.approx(5.0)
```

Run: `uv run pytest processor/tests/unit/test_consumer.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_processor.consumer'`.

- [ ] **Step 2: Implement the consumer and the SQS client**

`processor/src/shortener_processor/consumer.py`:
```python
"""Collect up to N messages within a short window, then process them (spec §5.3 step 1)."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from shortener_processor.batch import BatchOutcome
from shortener_processor.queue import QueueClient, ReceivedMessage

logger = logging.getLogger(__name__)
SQS_MAX_PER_RECEIVE = 10


class Processor(Protocol):
    async def process(self, messages: Sequence[ReceivedMessage]) -> BatchOutcome: ...


class Consumer:
    def __init__(
        self,
        queue: QueueClient,
        processor: Processor,
        *,
        max_messages: int = 100,
        window_seconds: float = 1.0,
        wait_seconds: int = 20,
        error_backoff_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._queue = queue
        self._processor = processor
        self._max = max_messages
        self._window = window_seconds
        self._wait = wait_seconds
        self._backoff = error_backoff_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._heartbeat = monotonic()

    def seconds_since_heartbeat(self) -> float:
        return self._monotonic() - self._heartbeat

    async def collect(self) -> list[ReceivedMessage]:
        batch: list[ReceivedMessage] = []
        deadline: float | None = None
        while len(batch) < self._max:
            if deadline is not None and self._monotonic() >= deadline:
                break
            wanted = min(SQS_MAX_PER_RECEIVE, self._max - len(batch))
            received = await self._queue.receive(wanted, self._wait if not batch else 1)
            self._heartbeat = self._monotonic()
            if not batch:
                if not received:
                    return []
                deadline = self._monotonic() + self._window
            batch.extend(received)
        return batch

    async def run_once(self) -> BatchOutcome | None:
        batch = await self.collect()
        if not batch:
            return None
        return await self._processor.process(batch)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # the loop must outlive any one batch (spec §9)
                logger.exception("click batch failed; undeleted messages will be redelivered")
                await self._sleep(self._backoff)
```

`processor/src/shortener_processor/sqs.py`:
```python
"""SQS adapter (ElasticMQ locally; Amazon SQS on AWS)."""

import asyncio
from collections.abc import Sequence
from typing import Any

from botocore.config import Config

from shortener_events import from_sqs_attributes
from shortener_processor.queue import ReceivedMessage

# read_timeout must exceed the 20 s long poll; bounded retries keep a sick SQS from stalling us.
SQS_CONFIG = Config(
    connect_timeout=2, read_timeout=25, retries={"total_max_attempts": 3, "mode": "standard"}
)
_DELETE_CHUNK = 10


class SqsQueueClient:
    def __init__(self, client: Any, queue_name: str) -> None:
        self._client = client
        self._queue_name = queue_name
        self._urls: dict[str, str] = {}

    async def _url(self, name: str) -> str:
        if name not in self._urls:
            response = await asyncio.to_thread(self._client.get_queue_url, QueueName=name)
            self._urls[name] = str(response["QueueUrl"])
        return self._urls[name]

    async def receive(self, max_messages: int, wait_seconds: int) -> list[ReceivedMessage]:
        response = await asyncio.to_thread(
            self._client.receive_message,
            QueueUrl=await self._url(self._queue_name),
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            MessageAttributeNames=["All"],
        )
        return [
            ReceivedMessage(
                message_id=str(m["MessageId"]),
                receipt_handle=str(m["ReceiptHandle"]),
                body=str(m["Body"]),
                attributes=from_sqs_attributes(m.get("MessageAttributes", {})),
            )
            for m in response.get("Messages", [])
        ]

    async def delete(self, receipt_handles: Sequence[str]) -> list[str]:
        url = await self._url(self._queue_name)
        failed: list[str] = []
        for start in range(0, len(receipt_handles), _DELETE_CHUNK):
            chunk = list(receipt_handles[start : start + _DELETE_CHUNK])
            response = await asyncio.to_thread(
                self._client.delete_message_batch,
                QueueUrl=url,
                Entries=[{"Id": str(i), "ReceiptHandle": h} for i, h in enumerate(chunk)],
            )
            failed += [chunk[int(f["Id"])] for f in response.get("Failed", [])]
        return failed

    async def depth(self, queue_name: str) -> int:
        response = await asyncio.to_thread(
            self._client.get_queue_attributes,
            QueueUrl=await self._url(queue_name),
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(response["Attributes"]["ApproximateNumberOfMessages"])
```

Run: `uv run pytest processor/tests/unit -q`
Expected: all PASS.

- [ ] **Step 3: Write the queue-level integration tests**

`processor/tests/integration/test_sqs.py`:
```python
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from shortener_events import ClickEvent, encode, to_sqs_attributes
from shortener_processor.batch import BatchProcessor
from shortener_processor.consumer import Consumer
from shortener_processor.link_resolver import PostgresLinkResolver
from shortener_processor.rollup_store import PostgresRollupStore
from shortener_processor.sqs import SqsQueueClient
from shortener_processor.telemetry import ProcessorTelemetry

pytestmark = pytest.mark.integration


def url(sqs, name="click-events"):
    return sqs.get_queue_url(QueueName=name)["QueueUrl"]


def send(sqs, body, attributes):
    sqs.send_message(
        QueueUrl=url(sqs), MessageBody=body, MessageAttributes=to_sqs_attributes(attributes)
    )


def click(code, link_id=None, n=0):
    return encode(
        ClickEvent(
            event_id=f"e-{code}-{n}",
            occurred_at=datetime.now(UTC),
            source="api",
            code=code,
            link_id=link_id,
            referrer="https://ref.example/x",
        )
    )


async def test_receive_and_delete_round_trip(sqs_client):
    message = click("aaaaaaa")
    send(sqs_client, message.body, message.attributes)
    queue = SqsQueueClient(sqs_client, "click-events")
    [received] = await queue.receive(10, 2)
    assert received.body == message.body
    assert received.attributes["type"] == "link.clicked"
    assert await queue.depth("click-events") == 0  # in flight, not visible
    assert await queue.delete([received.receipt_handle]) == []
    assert await queue.receive(10, 0) == []


async def test_bogus_receipt_handle_is_reported_as_failed(sqs_client):
    assert await SqsQueueClient(sqs_client, "click-events").delete(["not-a-handle"]) == [
        "not-a-handle"
    ]


async def test_poison_message_is_redriven_to_the_dlq(sqs_client):
    send(sqs_client, "{not json", {"type": "link.clicked", "version": "1"})
    queue = SqsQueueClient(sqs_client, "click-events")
    receives = 0
    for _ in range(8):  # maxReceiveCount=5: it must be gone by the 6th or 7th attempt
        got = await queue.receive(10, 1)
        if not got:
            break
        receives += 1
        sqs_client.change_message_visibility(
            QueueUrl=url(sqs_client), ReceiptHandle=got[0].receipt_handle, VisibilityTimeout=0
        )
    assert receives == 5
    assert await queue.depth("click-events-dlq") == 1


async def test_end_to_end_batch_against_real_queue_and_db(
    sqs_client, migrated, insert_link, meter, metric_value
):
    link = insert_link(code="e2e0001")
    for n in range(3):
        m = click("e2e0001", n=n)  # no link_id: exercises the resolver
        send(sqs_client, m.body, m.attributes)
    m = click("e2e0001", link_id=link, n=9)
    send(sqs_client, m.body, m.attributes)
    send(sqs_client, "{not json", {"type": "link.clicked", "version": "1"})

    engine = create_async_engine(migrated.url("processor_user"))
    queue = SqsQueueClient(sqs_client, "click-events")
    processor = BatchProcessor(
        queue, PostgresLinkResolver(engine), PostgresRollupStore(engine), ProcessorTelemetry(meter)
    )
    try:
        outcome = await Consumer(queue, processor, wait_seconds=2, window_seconds=0.5).run_once()
    finally:
        await engine.dispose()

    assert outcome is not None
    assert (outcome.ok, outcome.invalid) == (4, 1)
    with migrated.connect("migrator") as conn:
        assert conn.execute("SELECT sum(count) FROM analytics.link_clicks_hourly").fetchone() == (4,)
        assert conn.execute(
            "SELECT referrer_host, count FROM analytics.link_referrers_daily"
        ).fetchall() == [("ref.example", 4)]
    assert metric_value("shortener.processor.messages", {"result": "ok"}) == 4
    assert metric_value("shortener.processor.messages", {"result": "invalid"}) == 1
```

Run (colima env exported): `uv run pytest processor/tests/integration/test_sqs.py -q`
Expected: all PASS. The SQS client and consumer were implemented in Step 2, so prove the redrive test can fail: temporarily change `receives == 5` to `receives == 4`, confirm it fails, then revert. If ElasticMQ's redrive happens on a different receive count than 5, report the observed number. **Don't change `maxReceiveCount`.**

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff format . && make check && git add processor && git commit -m "feat(processor): SQS queue client and windowed batch consumer

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 6: Health server, queue-depth poller, and the service lifecycle

**Files:**
- Create: `processor/src/shortener_processor/{health,main,__main__}.py`
- Create: `processor/tests/unit/conftest.py` (settings fixture)
- Test: `processor/tests/unit/test_health.py`, `processor/tests/unit/test_main.py`

**Interfaces:**
- Consumes: `Consumer` (Task 5), `SqsQueueClient`/`SQS_CONFIG` (Task 5), `BatchProcessor` (Task 4), the store and resolver (Task 3), `ProcessorTelemetry`/`configure_meter_provider` (Task 4), `ProcessorSettings` (Task 2).
- Produces:
  - `health.Check = Callable[[], Awaitable[bool]]`
  - `health.start_health_server(host, port, *, live: Check, ready: Check) -> asyncio.Server`. It answers `GET /healthz` and `GET /readyz` with `200 {"status":"ok"}` or `503 {"status":"unavailable"}`, anything else with `404`. Always `Content-Type: application/json` and `Connection: close`.
  - `main.Runtime`: a kw-only dataclass with `settings`, `engine`, `queue: QueueClient`, `consumer: Consumer`, and `telemetry`
  - `main.build_runtime(settings) -> Runtime` (no network I/O at construction)
  - `main.poll_queue_depth(queue, names, telemetry, interval, stop, *, sleep=asyncio.sleep) -> None`. A failed depth call is logged and skipped, never raised.
  - `main.serve(runtime, stop: asyncio.Event) -> None`:
    - runs the consumer, the poller, and the health server
    - on `stop`, gives the consumer `settings.shutdown_grace_seconds` to finish, then cancels it
    - then cancels the poller, closes the server, and disposes the engine
  - `main.main()`: SIGTERM/SIGINT set `stop`. `python -m shortener_processor` calls `main()`.
  - Liveness: the consumer task isn't done **and** `seconds_since_heartbeat() < 3 × receive_wait_seconds` (minimum 10 s). Readiness: `SELECT 1` succeeds.

- [ ] **Step 1: Write the failing health-server tests**

`processor/tests/unit/test_health.py`:
```python
import httpx
import pytest

from shortener_processor.health import start_health_server


async def yes() -> bool:
    return True


async def no() -> bool:
    return False


async def boom() -> bool:
    raise RuntimeError("check crashed")


@pytest.fixture
async def serve_checks():
    servers = []

    async def _serve(live, ready) -> str:
        server = await start_health_server("127.0.0.1", 0, live=live, ready=ready)
        servers.append(server)
        port = server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield _serve
    for server in servers:
        server.close()
        await server.wait_closed()


async def test_ok_checks_return_200_json(serve_checks):
    base = await serve_checks(yes, yes)
    async with httpx.AsyncClient() as http:
        for path in ("/healthz", "/readyz"):
            response = await http.get(base + path)
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            assert response.json() == {"status": "ok"}


async def test_failing_checks_return_503(serve_checks):
    base = await serve_checks(no, no)
    async with httpx.AsyncClient() as http:
        assert (await http.get(base + "/healthz")).status_code == 503
        assert (await http.get(base + "/readyz")).json() == {"status": "unavailable"}


async def test_a_crashing_check_is_503_not_a_dropped_connection(serve_checks):
    base = await serve_checks(boom, yes)
    async with httpx.AsyncClient() as http:
        assert (await http.get(base + "/healthz")).status_code == 503


async def test_unknown_path_is_404(serve_checks):
    base = await serve_checks(yes, yes)
    async with httpx.AsyncClient() as http:
        assert (await http.get(base + "/metrics")).status_code == 404
```

Run: `uv run pytest processor/tests/unit/test_health.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Implement `health.py`**

`processor/src/shortener_processor/health.py`:
```python
"""Minimal stdlib HTTP health endpoints for the container healthcheck / load balancer."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)
Check = Callable[[], Awaitable[bool]]
_REASONS = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}
_READ_TIMEOUT = 5.0


async def _run_check(check: Check) -> bool:
    try:
        return await check()
    except Exception:
        logger.warning("health check raised", exc_info=True)
        return False


async def _handle(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, routes: dict[str, Check]
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), _READ_TIMEOUT)
        parts = request_line.decode("latin-1").split()
        path = parts[1] if len(parts) >= 2 else ""
        while (await asyncio.wait_for(reader.readline(), _READ_TIMEOUT)) not in (b"\r\n", b"\n", b""):
            pass  # discard headers
        check = routes.get(path)
        if check is None:
            status, body = 404, {"status": "not found"}
        elif await _run_check(check):
            status, body = 200, {"status": "ok"}
        else:
            status, body = 503, {"status": "unavailable"}
        payload = json.dumps(body).encode()
        head = (
            f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(head.encode() + payload)
        await writer.drain()
    except (TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


async def start_health_server(host: str, port: int, *, live: Check, ready: Check) -> asyncio.Server:
    routes = {"/healthz": live, "/readyz": ready}
    return await asyncio.start_server(lambda r, w: _handle(r, w, routes), host, port)
```

Run: `uv run pytest processor/tests/unit/test_health.py -q`
Expected: PASS.

- [ ] **Step 3: Write the failing lifecycle tests**

`processor/tests/unit/conftest.py`:
```python
import pytest

from shortener_processor.settings import ProcessorSettings


@pytest.fixture
def settings() -> ProcessorSettings:
    return ProcessorSettings(
        database_url="postgresql+psycopg://processor_user:x@127.0.0.1:1/shortener",
        sqs_endpoint_url="http://127.0.0.1:1",
        health_host="127.0.0.1",
        health_port=0,
        shutdown_grace_seconds=0.2,
        queue_depth_interval_seconds=0.01,
    )
```

`processor/tests/unit/test_main.py`:
```python
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

    await poll_queue_depth(queue, ["click-events", "click-events-dlq"], telemetry, 30, stop, sleep=sleep)
    assert queue.depth_calls == ["click-events", "click-events-dlq"]
    assert metric_value("shortener.queue.depth", {"queue": "click-events-dlq"}) == 3


async def test_poll_queue_depth_survives_errors(settings, meter):
    telemetry, stop = ProcessorTelemetry(meter), asyncio.Event()

    async def sleep(_: float) -> None:
        stop.set()

    await poll_queue_depth(IdleQueue(depth_error=True), ["click-events"], telemetry, 30, stop, sleep=sleep)


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
```
The two `serve` tests run a real event loop with short, bounded waits (≤ 0.05 s). That's the only way to exercise signal-driven shutdown, and it's an accepted exception to "no sleep in tests". Note it in the report.

Run: `uv run pytest processor/tests/unit/test_main.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_processor.main'`.

- [ ] **Step 4: Implement `main.py` and `__main__.py`**

`processor/src/shortener_processor/main.py`:
```python
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
    telemetry = ProcessorTelemetry(configure_meter_provider(settings).get_meter("shortener_processor"))
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    sqs = boto3.client(
        "sqs", region_name=settings.aws_region, endpoint_url=settings.sqs_endpoint_url, config=SQS_CONFIG
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
    return Runtime(settings=settings, engine=engine, queue=queue, consumer=consumer, telemetry=telemetry)


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


async def serve(runtime: Runtime, stop: asyncio.Event) -> None:
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

    server = await start_health_server(settings.health_host, settings.health_port, live=live, ready=ready)
    try:
        await stop.wait()
    finally:
        try:
            await asyncio.wait_for(asyncio.shield(consumer_task), settings.shutdown_grace_seconds)
        except TimeoutError:
            logger.warning("batch still running after %.0fs grace; abandoning it", settings.shutdown_grace_seconds)
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
```

`processor/src/shortener_processor/__main__.py`:
```python
from shortener_processor.main import main

main()
```

Run: `uv run pytest processor/tests/unit -q`
Expected: all PASS.

- [ ] **Step 5: Gates and commit**

```bash
uv run ruff format . && make check && git add processor && git commit -m "feat(processor): health server, queue-depth poller, graceful service lifecycle

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 7: Image, compose service, CI, end-to-end scenarios, docs

**Files:**
- Create: `processor/Dockerfile`, `tests/e2e/test_processor.py`
- Modify: `docker-compose.yml`, `Makefile` (`up`), `.github/workflows/ci.yml` (images matrix), `tests/e2e/conftest.py` (`processor_health_url`), `tests/e2e/test_api.py` (verify clicks through stats instead of SQS), `README.md`, `docs/superpowers/specs/2026-10-01-url-shortener-design.md`
- Test: `tests/e2e/test_processor.py`, the updated `tests/e2e/test_api.py`

**Interfaces:**
- Consumes: `python -m shortener_processor` (Task 6); the API's `/api/v1/links/{id}/stats` (Plan 2); `keycloak_tools.token.fetch_token` (Plan 1).
- Produces:
  - compose service `click-processor` (health on `localhost:8002`)
  - `E2ESettings.processor_health_url = "http://localhost:8002"`

**Why `test_api.py` must change:** its `test_link_lifecycle` reads the click event straight off `click-events`. With the processor now consuming that queue, the test would race the processor and flake. The click is now asserted where it really lands: in the stats endpoint. The queue purge goes away too (it was a deferred Plan 2 minor).

- [ ] **Step 1: Write the image and confirm the build**

`processor/Dockerfile`:
```dockerfile
# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
# Dependencies first (cached until a lockfile or pyproject changes), then the workspace source.
# Every workspace member's pyproject.toml must be listed here so uv can resolve the workspace.
COPY pyproject.toml uv.lock .python-version ./
COPY libs/shortener-events/pyproject.toml libs/shortener-events/
COPY libs/shortener-testing/pyproject.toml libs/shortener-testing/
COPY api/pyproject.toml api/
COPY processor/pyproject.toml processor/
COPY tools/keycloak-tools/pyproject.toml tools/keycloak-tools/
RUN uv sync --frozen --no-dev --no-install-workspace --package shortener-processor
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package shortener-processor

FROM python:3.12-slim
ARG GIT_SHA=dev
ENV PATH=/app/.venv/bin:$PATH SERVICE_VERSION=$GIT_SHA PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
USER 10001
EXPOSE 8002
CMD ["python", "-m", "shortener_processor"]
```

Run: `docker build -q -f processor/Dockerfile . && docker build -q -f api/Dockerfile . && docker build -q -f tools/keycloak-tools/Dockerfile .`
Expected: all three build. That confirms every Dockerfile lists every workspace member.

In `.github/workflows/ci.yml`, add `- processor/Dockerfile` to the `images` matrix.

- [ ] **Step 2: Write the failing end-to-end scenarios**

Add to `E2ESettings` in `tests/e2e/conftest.py`:
```python
    processor_health_url: str = "http://localhost:8002"
```

`tests/e2e/test_processor.py`:
```python
"""Click pipeline end to end: redirect → SQS → click-processor → rollups → stats API."""

import subprocess
import time
from pathlib import Path

import boto3
import httpx
import pytest

from keycloak_tools.token import fetch_token

pytestmark = pytest.mark.e2e
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def eddie(e2e_settings):
    token = fetch_token(e2e_settings.keycloak_url, "shortener", "eddie", "password")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def api(e2e_settings):
    with httpx.Client(base_url=e2e_settings.api_url, timeout=10, follow_redirects=False) as client:
        yield client


@pytest.fixture
def link(api, eddie):
    created = api.post("/api/v1/links", json={"target_url": "https://example.com/pipe"}, headers=eddie)
    assert created.status_code == 201, created.text
    yield created.json()
    api.delete(f"/api/v1/links/{created.json()['id']}", headers=eddie)


def wait_for_total(api, eddie, link_id, expected, timeout=30.0):
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = api.get(f"/api/v1/links/{link_id}/stats", headers=eddie).json()
        if body.get("total") == expected:
            return body
        time.sleep(0.5)
    raise AssertionError(f"stats total never reached {expected}: {body}")


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=REPO_ROOT, check=True, capture_output=True)


def test_processor_health(e2e_settings):
    assert httpx.get(f"{e2e_settings.processor_health_url}/healthz", timeout=5).status_code == 200
    assert httpx.get(f"{e2e_settings.processor_health_url}/readyz", timeout=5).status_code == 200


def test_clicks_reach_the_stats_api(api, eddie, link):
    for _ in range(3):
        assert api.get(f"/{link['code']}", headers={"Referer": "https://ref.example/a"}).status_code == 302
    body = wait_for_total(api, eddie, link["id"], 3)
    assert body["top_referrers"] == [{"referrer_host": "ref.example", "count": 3}]
    assert body["data_as_of"] is not None


def test_stopped_processor_builds_a_backlog_that_drains_on_restart(api, eddie, link, e2e_settings):
    sqs = boto3.client(
        "sqs",
        endpoint_url=e2e_settings.sqs_endpoint_url,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    url = sqs.get_queue_url(QueueName="click-events")["QueueUrl"]
    compose("stop", "click-processor")
    try:
        for _ in range(2):
            assert api.get(f"/{link['code']}").status_code == 302  # redirects keep working
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            depth = sqs.get_queue_attributes(
                QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
            )["Attributes"]["ApproximateNumberOfMessages"]
            if int(depth) >= 2:
                break
            time.sleep(0.5)
        assert int(depth) >= 2
        assert api.get(f"/api/v1/links/{link['id']}/stats", headers=eddie).json()["total"] == 0
    finally:
        compose("start", "click-processor")
    wait_for_total(api, eddie, link["id"], 2, timeout=45)
```
E2E tests may poll with `time.sleep`. Spec §15.2 forbids sleep only in unit and integration tests.

In `tests/e2e/test_api.py`:
- Delete the `queue` fixture, the `receive_click` helper, and the `boto3`/`shortener_events` imports.
- Remove `queue` from `test_link_lifecycle`'s parameters.
- Replace these lines:
```python
    event = receive_click(*queue, link["code"])
    assert str(event.link_id) == link["id"]
    assert event.referrer == "https://ref.example/x"
```
with:
```python
    deadline = time.monotonic() + 30
    stats = {}
    while time.monotonic() < deadline:  # the click-processor rolls the event up asynchronously
        stats = api.get(f"{url}/stats", headers=auth("eddie")).json()
        if stats.get("total") == 1:
            break
        time.sleep(0.5)
    assert stats["total"] == 1
    assert stats["top_referrers"] == [{"referrer_host": "ref.example", "count": 1}]
```
- Add `import time` at the top.

Run: `make e2e`
Expected: `test_processor.py` FAILS (nothing listens on `:8002`, and stats never move), and the updated `test_link_lifecycle` FAILS on the stats wait. Every other e2e test passes.

- [ ] **Step 3: Add the compose service and extend `make up`**

Add to `docker-compose.yml` under `services:` (after `api`):
```yaml
  click-processor:
    build:
      context: .
      dockerfile: processor/Dockerfile
      args:
        GIT_SHA: ${GIT_SHA:-dev}
    image: shortener-click-processor:${GIT_SHA:-dev}
    environment:
      DATABASE_URL: postgresql+psycopg://processor_user:${PROCESSOR_DB_PASSWORD:?set PROCESSOR_DB_PASSWORD in .env (see .env.example)}@postgres:5432/shortener
      SQS_ENDPOINT_URL: http://elasticmq:9324
      AWS_ACCESS_KEY_ID: local       # ElasticMQ ignores credentials; boto3 requires some
      AWS_SECRET_ACCESS_KEY: local
      SERVICE_VERSION: ${GIT_SHA:-dev}
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-lgtm:4318
      DEPLOYMENT_ENVIRONMENT: local
    ports:
      - "8002:8002"
    depends_on:
      postgres:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
      elasticmq:
        condition: service_started
    stop_grace_period: 30s
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/healthz', timeout=2)"]
      interval: 5s
      timeout: 3s
      retries: 20
      start_period: 5s
```

In the `Makefile`, change the last line of `up` to:
```make
	$(COMPOSE) up -d --build --wait api click-processor
```

Run:
```bash
make up
uv run pytest tests/test_env_example.py -q
make e2e
```
Expected: `click-processor` is healthy, the env contract test passes, and **every** e2e test passes, including both new processor scenarios and the updated lifecycle.

- [ ] **Step 4: Docs**

`README.md`:
- Add the services-table row `| Click processor | http://localhost:8002/healthz | Consumes click-events → analytics rollups |`.
- Replace the API section's sentence about events waiting in the queue with: "The click-processor rolls clicks into `analytics.*` within a second or two; `GET /api/v1/links/{id}/stats` shows them. Stop it (`docker compose stop click-processor`) and clicks queue up in `click-events` (http://localhost:9325); start it again and the backlog drains."

Spec `docs/superpowers/specs/2026-10-01-url-shortener-design.md`:
- **§3.3:** replace the `processor/` block's file list with the files this plan created:
  - `settings.py`, `referrers.py`, `aggregate.py`, `db.py`
  - `rollup_store.py`, `link_resolver.py`
  - `queue.py`, `sqs.py`, `batch.py`, `consumer.py`
  - `health.py`, `telemetry.py`, `main.py`

  Also add `libs/shortener-testing/  shared pytest fixtures plugin (Postgres + Alembic, ElasticMQ)` under the libs entries.
- **§5.3 step 4:** replace the sentence beginning "Rows whose link was deleted at the same moment…" with: "Links that no longer exist are detected inside the transaction and their events count as `unknown_link`. A link deleted after that check makes the transaction fail with a foreign-key violation, so the batch is retried after the visibility timeout. (`processor_user` cannot take row locks on `links`.)"
- **§5.3, after step 2:** add "Duplicate deliveries of the same `event_id` within one batch are counted once; every copy is deleted."

- [ ] **Step 5: Final gates and commit**

```bash
uv run ruff format . && make check && make e2e && git add processor/Dockerfile docker-compose.yml Makefile .github/workflows/ci.yml tests/e2e README.md docs/superpowers/specs/2026-10-01-url-shortener-design.md && git commit -m "feat(processor): compose service, CI image, e2e pipeline scenarios, docs

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

## Plan 3 Done When

- `make down && make up` brings up the stack with `click-processor` healthy on `:8002`.
- `make check` passes: lint, `mypy --strict`, tests, coverage ≥ 80% overall, and ≥ 90% on `referrers.py`, `aggregate.py`, and the existing pure modules.
- `make e2e` passes, showing:
  - redirects become stats within seconds
  - a stopped processor builds a backlog while redirects keep working, and it drains on restart
  - the API lifecycle scenario still holds
- Spec §3.3 and §5.3 reflect the deviations listed at the top of this plan.
