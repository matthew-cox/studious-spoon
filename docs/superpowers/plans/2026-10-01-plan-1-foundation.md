# Plan 1 — Foundation & Local Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the repo tooling, the shared click-event library, and the local platform (Postgres with schemas/grants via migrations, Keycloak realm + seeded users, ElasticMQ queues, otel-lgtm) so `make up` produces a working, verified foundation for the API, processor, and admin plans.

**Architecture:** A uv workspace with three members in this plan: `libs/shortener-events` (pure event contract + SQS codec), `api` (only settings + Alembic migrations for now; the API app arrives in Plan 2), and `tools/keycloak-tools` (idempotent user seeding + dev token helper). Docker Compose runs the backing services. A Postgres bootstrap script creates roles and databases only (what Terraform would do on RDS). Schemas, tables, and grants are created by Alembic, so the same migrations work locally and on RDS.

**Tech Stack:** Python 3.12, uv 0.12, pydantic 2 / pydantic-settings, SQLAlchemy 2 + Alembic + psycopg 3, httpx, PyYAML, pytest + testcontainers + respx, ruff, mypy (strict), Docker Compose, Postgres 16, Keycloak 26, ElasticMQ, grafana/otel-lgtm.

**Spec:** `docs/superpowers/specs/2026-10-01-url-shortener-design.md` (read §3, §4, §5.1, §7, §15 before starting).

**Plan series:** 1 Foundation (this) → 2 API → 3 Click processor → 4 Admin UI → 5 Observability dashboard, E2E, CI hardening. Each later plan is written after the previous one lands.

**Deviation from spec (intentional, documented):** user seeding lives in a typed, tested workspace package `tools/keycloak-tools` (module `keycloak_tools.seed`) instead of a loose `infra/keycloak/seed_users.py`. `infra/keycloak/users.yaml` stays where the spec puts it. Task 9 updates the spec's layout section to match.

## Global Constraints

- Python `>=3.12`; dependencies managed only with `uv`; `uv.lock` is committed; images install with `uv sync --frozen`.
- Config **only** from environment variables via one `pydantic-settings` class per entrypoint; missing or invalid config must fail at startup, naming the variable.
- No `if env == "prod"` branches.
- Every image is pinned to an exact tag (never `:latest`).
- The Postgres image tag appears in two places, which must match: `docker-compose.yml` and `api/tests/integration/conftest.py`.
- Migrations run only via the `migrate` job / `alembic upgrade head`, never at app startup.
- DB roles are exactly `migrator`, `api_user`, `processor_user`, `admin_user` (plus `keycloak` for Keycloak's own DB). Grants follow spec §3.4.
- Realm `shortener`; realm roles `admin`, `editor`, `viewer`; clients `shortener-admin`, `shortener-api`, `shortener-dev`.
- `KC_HOSTNAME=http://localhost:8080`, so the token issuer is always `http://localhost:8080/realms/shortener`.
- Queues `click-events` (visibility timeout 30 s, max receive wait 20 s, redrive to `click-events-dlq` after `maxReceiveCount=5`) and `click-events-dlq`.
- Business-logic modules must not import FastAPI, SQLAlchemy, boto, or httpx. In this plan that's `shortener_events.*`, `keycloak_tools.users`, and `keycloak_tools.plan`.
- Prefer hand-written fakes over mocks; `respx` only at outside HTTP boundaries.
- No SQLite. No `sleep` in unit/integration tests.
- Quality gates: `ruff check`, `ruff format --check`, `mypy --strict` on every package's `src/`, ≥80% overall branch coverage, ≥90% branch coverage on pure modules.
- No new `# type: ignore` / `noqa` without an inline reason.
- Dev-only secrets live in `.env.example` and `infra/keycloak/users.yaml`, each labeled `DEV ONLY`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Re-running `make up` on an existing stack** (realm already imported, users already seeded, migrations already applied) must succeed with no changes and no duplicate users. Tests: Task 4 `test_upgrade_is_idempotent`, Task 8 `test_second_run_makes_no_changes` (unit) and `test_reseed_is_noop` (e2e).
2. **Keycloak users with roles the seeder doesn't manage** (`default-roles-shortener`, `offline_access`, `uma_authorization`) must never have those roles removed. Test: Task 8 `test_unmanaged_roles_are_never_removed`.
3. **Hostile or odd click metadata:** a non-UTC timestamp is normalized to UTC; oversized `Referer`/`User-Agent` values are truncated, not rejected; empty strings become `null`; naive timestamps are rejected. Tests: Task 2 `test_model.py`.
4. **Messages that aren't ours or aren't v1** (wrong `type`, missing or non-numeric `version`, `version=2`, malformed JSON) must raise a specific `InvalidEventError` / `UnsupportedEventVersionError`, so the processor can leave them for the DLQ instead of crashing. Tests: Task 2 `test_codec.py`.
5. **DB roles crossing schema boundaries** (`admin_user` reading `links`, `processor_user` reading `target_url` or writing `links`, `api_user` writing `analytics`) must get `permission denied`. Tests: Task 4 `test_migrations.py` privilege tests.

---

## File Structure

```
.gitignore, .dockerignore, .env.example, Makefile, README.md, pyproject.toml (workspace root), uv.lock
docker-compose.yml
libs/shortener-events/
  pyproject.toml
  schema/link.clicked.v1.json                 generated, committed; drift-tested
  src/shortener_events/__init__.py            public exports
  src/shortener_events/model.py               ClickEvent (pydantic, frozen)
  src/shortener_events/codec.py               SqsMessage, encode/decode, attribute converters, errors
  src/shortener_events/schema.py              json_schema() + CLI to regenerate the committed file
  tests/conftest.py, test_model.py, test_codec.py, test_schema.py
api/
  pyproject.toml, Dockerfile, alembic.ini
  alembic/env.py, alembic/script.py.mako, alembic/versions/0001_initial_schema.py
  src/shortener_api/__init__.py
  src/shortener_api/settings.py               MigrateSettings + load_migrate_settings()
  tests/unit/test_settings.py
  tests/integration/conftest.py, tests/integration/test_migrations.py
tools/keycloak-tools/
  pyproject.toml, Dockerfile
  src/keycloak_tools/__init__.py
  src/keycloak_tools/users.py                 DesiredUser, load_users, MANAGED_ROLES (pure)
  src/keycloak_tools/plan.py                  ExistingUser, Action types, plan_changes (pure)
  src/keycloak_tools/admin_client.py          KeycloakAdmin protocol + httpx implementation
  src/keycloak_tools/seed.py                  SeedSettings, run_seed, main
  src/keycloak_tools/token.py                 fetch_token + CLI (make token)
  tests/test_users.py, test_plan.py, test_seed.py, test_admin_client.py, test_token.py
infra/
  postgres/bootstrap.sql, postgres/init.sh
  elasticmq/elasticmq.conf
  keycloak/realm-export.json, keycloak/users.yaml
tests/e2e/conftest.py, test_database.py, test_queues.py, test_realm.py, test_seed.py, test_observability.py
.github/workflows/ci.yml
```

---

### Task 1: Workspace and tooling bootstrap

**Files:**
- Create: `.gitignore`, `.dockerignore`, `pyproject.toml`, `Makefile`
- Note: the workspace lists only `libs/shortener-events`, which Task 2 creates; `uv lock` runs in Task 2.

**Interfaces:**
- Produces: Make targets `sync`, `lint`, `fmt`, `typecheck`, `test`, `check`, `up`, `down`, `logs`, `migrate`, `seed-users`, `token`, `e2e` (later tasks make them work); root pytest config with markers `integration` and `e2e`; `e2e` is excluded by default.

- [ ] **Step 1: Check local prerequisites**

Run:
```bash
uv --version            # expect 0.12.x or newer
docker compose version  # expect v2.20 or newer (needed for `up --wait`)
colima status || true   # if using colima: `colima start --cpu 4 --memory 8` (Keycloak + LGTM need ~6 GB)
```
Expected: versions print. If `docker compose` is missing or older than v2.20, install/upgrade the Compose plugin before continuing.

- [ ] **Step 2: Resolve and record the image tags**

Run each command and confirm it prints a manifest (no "no such manifest"):
```bash
docker manifest inspect postgres:16.10-alpine >/dev/null && echo ok
docker manifest inspect quay.io/keycloak/keycloak:26.4.0 >/dev/null && echo ok
docker manifest inspect softwaremill/elasticmq-native:1.6.14 >/dev/null && echo ok
docker manifest inspect grafana/otel-lgtm:0.11.10 >/dev/null && echo ok
docker manifest inspect ghcr.io/astral-sh/uv:0.12.1 >/dev/null && echo ok
docker manifest inspect python:3.12-slim >/dev/null && echo ok
```
If a tag doesn't exist, use the newest existing patch of the same minor line (check the registry's tag list). Use the confirmed tags **everywhere this plan shows these tags**. Keep `python:3.12-slim` as is; it's a moving minor tag, which is acceptable for the base image here.

- [ ] **Step 3: Write `.gitignore` and `.dockerignore`**

`.gitignore`:
```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage.xml
htmlcov/
.env
dist/
build/
*.egg-info/
.claude/settings.local.json
```

`.dockerignore`:
```gitignore
.git
.venv
**/__pycache__
**/.pytest_cache
**/.mypy_cache
**/.ruff_cache
**/tests
.env
.coverage
coverage.xml
htmlcov
docs
.claude
```

- [ ] **Step 4: Write the root `pyproject.toml`**

```toml
[project]
name = "platform-url-shortener"
version = "0.0.0"
requires-python = ">=3.12"

[tool.uv]
package = false

[tool.uv.workspace]
members = ["libs/shortener-events"]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "pytest-cov>=5.0",
  "respx>=0.21",
  "testcontainers[postgres]>=4.8",
  "psycopg[binary]>=3.2",
  "boto3>=1.35",
  "httpx>=0.27",
  "pydantic-settings>=2.4",
  "mypy>=1.11",
  "ruff>=0.6",
  "types-PyYAML>=6.0",
  "boto3-stubs[sqs]>=1.35",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "SIM", "RUF"]

[tool.ruff.lint.per-file-ignores]
"**/tests/**" = ["S101", "S105", "S106", "S603", "S607"]
"**/alembic/versions/**" = ["E501"]

[tool.mypy]
strict = true
python_version = "3.12"
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
addopts = "--import-mode=importlib -m 'not e2e'"
asyncio_mode = "auto"
testpaths = ["libs", "api", "tools", "tests"]
markers = [
  "integration: needs a Docker daemon (testcontainers)",
  "e2e: needs the full compose stack running (make up)",
]

[tool.coverage.run]
branch = true
source = ["shortener_events", "shortener_api", "keycloak_tools"]

[tool.coverage.report]
show_missing = true
skip_covered = true
```

- [ ] **Step 5: Write the `Makefile`**

```make
SHELL := /bin/bash
COMPOSE := docker compose
GIT_SHA ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
export GIT_SHA

# Pure business-logic modules: >= 90% branch coverage (spec §15.2). Later plans append to this list.
PURE_MODULES := */shortener_events/*,*/keycloak_tools/users.py,*/keycloak_tools/plan.py
MYPY_TARGETS := libs/shortener-events/src

USER ?= alice

.PHONY: sync lint fmt typecheck test check up down logs migrate seed-users token e2e

sync:
	uv sync --all-packages --frozen

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy $(MYPY_TARGETS)

test:
	uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=80
	uv run coverage report --include='$(PURE_MODULES)' --fail-under=90

check: lint typecheck test

.env:
	cp .env.example .env

up: .env
	$(COMPOSE) up -d --build --wait postgres
	$(COMPOSE) run --rm --build migrate

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f

migrate: .env
	$(COMPOSE) run --rm --build migrate

seed-users: .env
	$(COMPOSE) run --rm --build keycloak-seed

token:
	@uv run python -m keycloak_tools.token $(USER)

e2e:
	uv run pytest -m e2e tests/e2e -v
```
(The `up` target grows in Tasks 6–9 as services are added. `MYPY_TARGETS` grows in Tasks 4 and 8.)

- [ ] **Step 6: Verify formatting tooling runs on the empty repo**

Run: `uvx ruff@0.6.9 check . && uvx ruff@0.6.9 format --check .`
Expected: `All checks passed!` and no files to format. (Full `make lint` works after Task 2 creates the lockfile.)

- [ ] **Step 7: Commit**

```bash
git add .gitignore .dockerignore pyproject.toml Makefile
git commit -m "chore: bootstrap uv workspace, lint/type/test tooling, Makefile

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Click event library (`shortener-events`)

**Files:**
- Create: `libs/shortener-events/pyproject.toml`
- Create: `libs/shortener-events/src/shortener_events/{__init__,model,codec,schema}.py`, `libs/shortener-events/src/shortener_events/py.typed` (empty)
- Create: `libs/shortener-events/schema/link.clicked.v1.json` (generated)
- Test: `libs/shortener-events/tests/{conftest,test_model,test_codec,test_schema}.py`
- Create: `uv.lock` (via `uv lock`)

**Interfaces:**
- Produces (used by Plans 2 and 3):
  - `shortener_events.ClickEvent`: frozen pydantic model with fields `type: Literal["link.clicked"]`, `version: Literal[1]`, `event_id: str`, `occurred_at: datetime` (aware, normalized to UTC), `source: Literal["api", "cloudfront"]`, `code: str`, `link_id: UUID | None`, `referrer: str | None`, `user_agent: str | None`
  - `shortener_events.SqsMessage(body: str, attributes: dict[str, str])`
  - `shortener_events.encode(event: ClickEvent, traceparent: str | None = None) -> SqsMessage`
  - `shortener_events.decode(message: SqsMessage) -> ClickEvent`, which raises `InvalidEventError` or `UnsupportedEventVersionError` (a subclass of `InvalidEventError`)
  - `shortener_events.to_sqs_attributes(attrs: Mapping[str, str]) -> dict[str, dict[str, str]]`
  - `shortener_events.from_sqs_attributes(raw: Mapping[str, Mapping[str, Any]]) -> dict[str, str]`
  - Constants `EVENT_TYPE = "link.clicked"`, `REFERRER_MAX = 1024`, `USER_AGENT_MAX = 512`

- [ ] **Step 1: Create the package metadata and register it**

`libs/shortener-events/pyproject.toml`:
```toml
[project]
name = "shortener-events"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.8"]

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shortener_events"]
```
Create `libs/shortener-events/src/shortener_events/__init__.py` containing only `"""Click event contract shared by producers and the click processor."""` for now, plus an empty `py.typed`.

Run: `uv lock && uv sync --all-packages`
Expected: lockfile created; the environment includes `shortener-events` and the dev group.

- [ ] **Step 2: Write the failing model tests**

`libs/shortener-events/tests/conftest.py`:
```python
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from shortener_events import ClickEvent

LINK_ID = UUID("6f1c2d3e-4b5a-4c6d-8e7f-001122334455")


@pytest.fixture
def make_event() -> Callable[..., ClickEvent]:
    def _make(**overrides: Any) -> ClickEvent:
        fields: dict[str, Any] = {
            "event_id": "evt-1",
            "occurred_at": datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
            "source": "api",
            "code": "aZ3kQ9x",
            "link_id": LINK_ID,
            "referrer": "https://news.ycombinator.com/item?id=1",
            "user_agent": "Mozilla/5.0",
        }
        fields.update(overrides)
        return ClickEvent(**fields)

    return _make
```

`libs/shortener-events/tests/test_model.py`:
```python
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from shortener_events import REFERRER_MAX, USER_AGENT_MAX


def test_type_and_version_default(make_event):
    event = make_event()
    assert event.type == "link.clicked"
    assert event.version == 1


def test_naive_occurred_at_is_rejected(make_event):
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_event(occurred_at=datetime(2026, 10, 1, 12, 0))


def test_non_utc_occurred_at_is_normalized_to_utc(make_event):
    eastern = timezone(timedelta(hours=-4))
    event = make_event(occurred_at=datetime(2026, 10, 1, 8, 0, tzinfo=eastern))
    assert event.occurred_at == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert event.occurred_at.utcoffset() == timedelta(0)


def test_oversized_referrer_is_truncated_not_rejected(make_event):
    event = make_event(referrer="https://example.com/" + "a" * 5000)
    assert event.referrer is not None
    assert len(event.referrer) == REFERRER_MAX


def test_oversized_user_agent_is_truncated_not_rejected(make_event):
    event = make_event(user_agent="x" * 5000)
    assert event.user_agent is not None
    assert len(event.user_agent) == USER_AGENT_MAX


def test_empty_referrer_and_user_agent_become_none(make_event):
    event = make_event(referrer="", user_agent="")
    assert event.referrer is None
    assert event.user_agent is None


def test_link_id_is_optional(make_event):
    assert make_event(link_id=None).link_id is None


def test_unknown_source_is_rejected(make_event):
    with pytest.raises(ValidationError):
        make_event(source="lambda")


@pytest.mark.parametrize("code", ["", "x" * 33])
def test_code_length_is_bounded(make_event, code):
    with pytest.raises(ValidationError):
        make_event(code=code)


def test_unknown_fields_are_rejected(make_event):
    with pytest.raises(ValidationError):
        make_event(country="CA")


def test_event_is_immutable(make_event):
    event = make_event()
    with pytest.raises(ValidationError):
        event.code = "other"
```

- [ ] **Step 3: Run the model tests and watch them fail**

Run: `uv run pytest libs/shortener-events/tests/test_model.py -v`
Expected: collection error / FAIL with `ImportError: cannot import name 'ClickEvent'`.

- [ ] **Step 4: Implement the model**

`libs/shortener-events/src/shortener_events/model.py`:
```python
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVENT_TYPE = "link.clicked"
REFERRER_MAX = 1024
USER_AGENT_MAX = 512


def _truncate(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit]


class ClickEvent(BaseModel):
    """A single redirect served to a visitor (`link.clicked`, version 1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["link.clicked"] = "link.clicked"
    version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    source: Literal["api", "cloudfront"]
    code: str = Field(min_length=1, max_length=32)
    link_id: UUID | None = None
    referrer: str | None = None
    user_agent: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("referrer")
    @classmethod
    def _truncate_referrer(cls, value: str | None) -> str | None:
        return _truncate(value, REFERRER_MAX)

    @field_validator("user_agent")
    @classmethod
    def _truncate_user_agent(cls, value: str | None) -> str | None:
        return _truncate(value, USER_AGENT_MAX)
```

`libs/shortener-events/src/shortener_events/__init__.py`:
```python
"""Click event contract shared by producers and the click processor."""

from shortener_events.model import EVENT_TYPE, REFERRER_MAX, USER_AGENT_MAX, ClickEvent

__all__ = ["EVENT_TYPE", "REFERRER_MAX", "USER_AGENT_MAX", "ClickEvent"]
```

- [ ] **Step 5: Run the model tests and confirm they pass**

Run: `uv run pytest libs/shortener-events/tests/test_model.py -v`
Expected: all PASS.

- [ ] **Step 6: Write the failing codec tests**

`libs/shortener-events/tests/test_codec.py`:
```python
import pytest

from shortener_events import (
    InvalidEventError,
    SqsMessage,
    UnsupportedEventVersionError,
    decode,
    encode,
    from_sqs_attributes,
    to_sqs_attributes,
)

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_round_trip_preserves_event(make_event):
    event = make_event()
    assert decode(encode(event, traceparent=TRACEPARENT)) == event


def test_encode_sets_type_version_and_traceparent_attributes(make_event):
    message = encode(make_event(), traceparent=TRACEPARENT)
    assert message.attributes == {
        "type": "link.clicked",
        "version": "1",
        "traceparent": TRACEPARENT,
    }


def test_encode_without_traceparent_omits_it(make_event):
    assert "traceparent" not in encode(make_event()).attributes


def test_sqs_attribute_conversion_round_trips():
    attrs = {"type": "link.clicked", "version": "1"}
    raw = to_sqs_attributes(attrs)
    assert raw["type"] == {"DataType": "String", "StringValue": "link.clicked"}
    assert from_sqs_attributes(raw) == attrs


def test_from_sqs_attributes_ignores_non_string_values():
    raw = {"blob": {"DataType": "Binary", "BinaryValue": b"\x00"}}
    assert from_sqs_attributes(raw) == {}


def test_wrong_type_is_invalid(make_event):
    body = encode(make_event()).body
    with pytest.raises(InvalidEventError, match="type"):
        decode(SqsMessage(body=body, attributes={"type": "link.created", "version": "1"}))


def test_missing_attributes_are_invalid(make_event):
    with pytest.raises(InvalidEventError):
        decode(SqsMessage(body=encode(make_event()).body, attributes={}))


def test_non_numeric_version_is_invalid(make_event):
    body = encode(make_event()).body
    with pytest.raises(InvalidEventError, match="version"):
        decode(SqsMessage(body=body, attributes={"type": "link.clicked", "version": "one"}))


def test_unknown_version_is_unsupported(make_event):
    body = encode(make_event()).body
    with pytest.raises(UnsupportedEventVersionError):
        decode(SqsMessage(body=body, attributes={"type": "link.clicked", "version": "2"}))


def test_unsupported_version_is_an_invalid_event():
    assert issubclass(UnsupportedEventVersionError, InvalidEventError)


def test_malformed_json_is_invalid():
    with pytest.raises(InvalidEventError):
        decode(SqsMessage(body="{not json", attributes={"type": "link.clicked", "version": "1"}))


def test_body_failing_validation_is_invalid():
    body = (
        '{"type":"link.clicked","version":1,"event_id":"e","occurred_at":"2026-10-01T12:00:00",'
        '"source":"api","code":"abc"}'
    )
    with pytest.raises(InvalidEventError):
        decode(SqsMessage(body=body, attributes={"type": "link.clicked", "version": "1"}))
```

- [ ] **Step 7: Run the codec tests and watch them fail**

Run: `uv run pytest libs/shortener-events/tests/test_codec.py -v`
Expected: FAIL with `ImportError: cannot import name 'InvalidEventError'`.

- [ ] **Step 8: Implement the codec**

`libs/shortener-events/src/shortener_events/codec.py`:
```python
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from shortener_events.model import EVENT_TYPE, ClickEvent

SUPPORTED_VERSIONS = frozenset({1})


class InvalidEventError(ValueError):
    """The message is not a valid `link.clicked` event this code understands."""


class UnsupportedEventVersionError(InvalidEventError):
    """The message is a `link.clicked` event of a version this code does not support."""


@dataclass(frozen=True)
class SqsMessage:
    body: str
    attributes: dict[str, str] = field(default_factory=dict)


def encode(event: ClickEvent, traceparent: str | None = None) -> SqsMessage:
    attributes = {"type": event.type, "version": str(event.version)}
    if traceparent:
        attributes["traceparent"] = traceparent
    return SqsMessage(body=event.model_dump_json(), attributes=attributes)


def decode(message: SqsMessage) -> ClickEvent:
    event_type = message.attributes.get("type")
    if event_type != EVENT_TYPE:
        raise InvalidEventError(f"unexpected event type: {event_type!r}")
    raw_version = message.attributes.get("version")
    try:
        version = int(raw_version) if raw_version is not None else None
    except ValueError as exc:
        raise InvalidEventError(f"non-numeric version: {raw_version!r}") from exc
    if version is None:
        raise InvalidEventError("missing version attribute")
    if version not in SUPPORTED_VERSIONS:
        raise UnsupportedEventVersionError(f"unsupported version: {version}")
    try:
        return ClickEvent.model_validate_json(message.body)
    except ValidationError as exc:
        raise InvalidEventError(str(exc)) from exc


def to_sqs_attributes(attrs: Mapping[str, str]) -> dict[str, dict[str, str]]:
    return {key: {"DataType": "String", "StringValue": value} for key, value in attrs.items()}


def from_sqs_attributes(raw: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    return {
        key: str(value["StringValue"])
        for key, value in raw.items()
        if "StringValue" in value
    }
```

Replace `libs/shortener-events/src/shortener_events/__init__.py` with:
```python
"""Click event contract shared by producers and the click processor."""

from shortener_events.codec import (
    InvalidEventError,
    SqsMessage,
    UnsupportedEventVersionError,
    decode,
    encode,
    from_sqs_attributes,
    to_sqs_attributes,
)
from shortener_events.model import EVENT_TYPE, REFERRER_MAX, USER_AGENT_MAX, ClickEvent

__all__ = [
    "EVENT_TYPE",
    "REFERRER_MAX",
    "USER_AGENT_MAX",
    "ClickEvent",
    "InvalidEventError",
    "SqsMessage",
    "UnsupportedEventVersionError",
    "decode",
    "encode",
    "from_sqs_attributes",
    "to_sqs_attributes",
]
```

- [ ] **Step 9: Run the codec tests and confirm they pass**

Run: `uv run pytest libs/shortener-events/tests -v`
Expected: all PASS.

- [ ] **Step 10: Write the failing schema-drift test**

`libs/shortener-events/tests/test_schema.py`:
```python
import json
from pathlib import Path

from shortener_events.schema import json_schema

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "link.clicked.v1.json"


def test_committed_schema_matches_model():
    assert json.loads(SCHEMA_PATH.read_text()) == json_schema()
```

Run: `uv run pytest libs/shortener-events/tests/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_events.schema'`.

- [ ] **Step 11: Implement the schema export and generate the committed file**

`libs/shortener-events/src/shortener_events/schema.py`:
```python
"""JSON Schema for `link.clicked` v1. Regenerate the committed copy with:

    uv run python -m shortener_events.schema > libs/shortener-events/schema/link.clicked.v1.json
"""

import json
from typing import Any

from shortener_events.model import ClickEvent


def json_schema() -> dict[str, Any]:
    return ClickEvent.model_json_schema(mode="serialization")


def main() -> None:
    print(json.dumps(json_schema(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
```

Run:
```bash
mkdir -p libs/shortener-events/schema
uv run python -m shortener_events.schema > libs/shortener-events/schema/link.clicked.v1.json
uv run pytest libs/shortener-events/tests -v
```
Expected: all PASS.

- [ ] **Step 12: Run the quality gates for this package**

Run: `make lint typecheck && uv run pytest libs --cov --cov-fail-under=90`
Expected: lint clean, `Success: no issues found`, coverage ≥ 90%.

- [ ] **Step 13: Commit**

```bash
git add libs/shortener-events pyproject.toml uv.lock
git commit -m "feat(events): add link.clicked v1 contract, SQS codec, and JSON schema

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Postgres bootstrap (roles and databases) and compose service

**Files:**
- Create: `infra/postgres/bootstrap.sql`, `infra/postgres/init.sh`
- Create: `.env.example`, `docker-compose.yml`

**Interfaces:**
- Produces: roles `migrator`, `api_user`, `processor_user`, `admin_user`, `keycloak`; databases `shortener` (owner `migrator`; `CONNECT` only for the three app roles) and `keycloak` (owner `keycloak`). `bootstrap.sql` takes psql variables `migrator_pw`, `api_pw`, `processor_pw`, `admin_pw`, `keycloak_pw`. Task 4's test fixture runs the same file.

- [ ] **Step 1: Write the bootstrap SQL**

`infra/postgres/bootstrap.sql`:
```sql
-- Creates roles and databases only. On RDS, the equivalent runs once from Terraform/bootstrap.
-- Schemas, tables, and grants are owned by Alembic migrations (api/alembic).
-- Requires psql variables: migrator_pw, api_pw, processor_pw, admin_pw, keycloak_pw.

CREATE ROLE migrator LOGIN PASSWORD :'migrator_pw';
CREATE ROLE api_user LOGIN PASSWORD :'api_pw';
CREATE ROLE processor_user LOGIN PASSWORD :'processor_pw';
CREATE ROLE admin_user LOGIN PASSWORD :'admin_pw';
CREATE ROLE keycloak LOGIN PASSWORD :'keycloak_pw';

CREATE DATABASE keycloak OWNER keycloak;
CREATE DATABASE shortener OWNER migrator;

REVOKE CONNECT ON DATABASE shortener FROM PUBLIC;
GRANT CONNECT ON DATABASE shortener TO api_user, processor_user, admin_user;
```

`infra/postgres/init.sh` (make it executable: `chmod +x infra/postgres/init.sh`):
```bash
#!/usr/bin/env bash
# Runs once, on first start of an empty data volume (docker-entrypoint-initdb.d).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v migrator_pw="$MIGRATOR_DB_PASSWORD" \
  -v api_pw="$API_DB_PASSWORD" \
  -v processor_pw="$PROCESSOR_DB_PASSWORD" \
  -v admin_pw="$ADMIN_DB_PASSWORD" \
  -v keycloak_pw="$KEYCLOAK_DB_PASSWORD" \
  -f /bootstrap/bootstrap.sql
```

- [ ] **Step 2: Write `.env.example`**

```dotenv
# DEV ONLY — local credentials for docker compose. Never reuse these outside your machine.
COMPOSE_PROJECT_NAME=shortener

POSTGRES_SUPERUSER_PASSWORD=postgres-dev
MIGRATOR_DB_PASSWORD=migrator-dev
API_DB_PASSWORD=api-dev
PROCESSOR_DB_PASSWORD=processor-dev
ADMIN_DB_PASSWORD=admin-dev
KEYCLOAK_DB_PASSWORD=keycloak-dev

KEYCLOAK_ADMIN_USER=kcadmin
KEYCLOAK_ADMIN_PASSWORD=kcadmin-dev
SHORTENER_ADMIN_CLIENT_SECRET=shortener-admin-dev-secret
```

- [ ] **Step 3: Write the initial `docker-compose.yml`**

```yaml
name: shortener

services:
  postgres:
    image: postgres:16.10-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_SUPERUSER_PASSWORD}
      POSTGRES_DB: postgres
      MIGRATOR_DB_PASSWORD: ${MIGRATOR_DB_PASSWORD}
      API_DB_PASSWORD: ${API_DB_PASSWORD}
      PROCESSOR_DB_PASSWORD: ${PROCESSOR_DB_PASSWORD}
      ADMIN_DB_PASSWORD: ${ADMIN_DB_PASSWORD}
      KEYCLOAK_DB_PASSWORD: ${KEYCLOAK_DB_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./infra/postgres:/bootstrap:ro
      - ./infra/postgres/init.sh:/docker-entrypoint-initdb.d/10-bootstrap.sh:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d shortener"]
      interval: 5s
      timeout: 3s
      retries: 20

volumes:
  pgdata:
```
(The healthcheck targets `shortener`, so the service only reports healthy after the bootstrap has created that database.)

- [ ] **Step 4: Verify the bootstrap**

Run:
```bash
cp -n .env.example .env
docker compose up -d --wait postgres
docker compose exec postgres psql -U postgres -tAc "select rolname from pg_roles where rolname in ('migrator','api_user','processor_user','admin_user','keycloak') order by 1"
docker compose exec postgres psql -U postgres -tAc "select datname, pg_get_userbyid(datdba) from pg_database where datname in ('shortener','keycloak') order by 1"
```
Expected: the five roles listed. Then `keycloak|keycloak` and `shortener|migrator`.

- [ ] **Step 5: Commit**

```bash
git add infra/postgres .env.example docker-compose.yml
git commit -m "feat(infra): postgres bootstrap for roles and databases

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: API package skeleton — settings, Alembic, initial schema and grants

**Files:**
- Create: `api/pyproject.toml`, `api/src/shortener_api/__init__.py`, `api/src/shortener_api/py.typed`, `api/src/shortener_api/settings.py`
- Create: `api/alembic.ini`, `api/alembic/env.py`, `api/alembic/script.py.mako`, `api/alembic/versions/0001_initial_schema.py`
- Modify: `pyproject.toml` (workspace members), `Makefile` (`MYPY_TARGETS`)
- Test: `api/tests/unit/test_settings.py`, `api/tests/integration/conftest.py`, `api/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: `infra/postgres/bootstrap.sql` (Task 3).
- Produces:
  - `shortener_api.settings.MigrateSettings` (field `migrator_database_url: PostgresDsn`, env `MIGRATOR_DATABASE_URL`)
  - `shortener_api.settings.load_migrate_settings() -> MigrateSettings`
  - Alembic revision `"0001"`, which creates `public.links`, `analytics.link_clicks_hourly`, `analytics.link_referrers_daily`, `analytics.pipeline_status` (seeded with row `id=1`), `admin.sessions`, and the grants from spec §3.4. Column names and types are exactly as in spec §4.1/§4.2/§4.5.
  - Test fixtures `pg_server` (with `.url(user, db=...)`, `.connect(user, db=...)`), `migrated`, and `insert_link`. Plan 2 reuses these.

- [ ] **Step 1: Create the package and register it in the workspace**

`api/pyproject.toml`:
```toml
[project]
name = "shortener-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "shortener-events",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy>=2.0.35",
  "alembic>=1.13",
  "psycopg[binary]>=3.2",
]

[tool.uv.sources]
shortener-events = { workspace = true }

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shortener_api"]
```
Create `api/src/shortener_api/__init__.py` with `"""URL shortener API service."""` and an empty `py.typed`.

In the root `pyproject.toml`, set `members = ["libs/shortener-events", "api"]`.
In the `Makefile`, set `MYPY_TARGETS := libs/shortener-events/src api/src`.

Run: `uv lock && uv sync --all-packages`
Expected: success.

- [ ] **Step 2: Write the failing settings tests**

`api/tests/unit/test_settings.py`:
```python
import pytest
from pydantic import ValidationError

from shortener_api.settings import load_migrate_settings


def test_missing_url_fails_fast_naming_the_variable(monkeypatch):
    monkeypatch.delenv("MIGRATOR_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="migrator_database_url"):
        load_migrate_settings()


def test_reads_url_from_environment(monkeypatch):
    url = "postgresql+psycopg://migrator:pw@db:5432/shortener"
    monkeypatch.setenv("MIGRATOR_DATABASE_URL", url)
    assert str(load_migrate_settings().migrator_database_url) == url


def test_rejects_non_postgres_url(monkeypatch):
    monkeypatch.setenv("MIGRATOR_DATABASE_URL", "mysql://root@db/shortener")
    with pytest.raises(ValidationError):
        load_migrate_settings()
```

Run: `uv run pytest api/tests/unit -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_api.settings'`.

- [ ] **Step 3: Implement the settings**

`api/src/shortener_api/settings.py`:
```python
from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrateSettings(BaseSettings):
    """Configuration for the `migrate` release job (alembic)."""

    model_config = SettingsConfigDict(extra="ignore")

    migrator_database_url: PostgresDsn


def load_migrate_settings() -> MigrateSettings:
    # pydantic-settings fills required fields from the environment; mypy can't see that.
    return MigrateSettings()  # type: ignore[call-arg]
```

Run: `uv run pytest api/tests/unit -v`
Expected: all PASS.

- [ ] **Step 4: Write the Alembic scaffolding**

`api/alembic.ini`:
```ini
[alembic]
script_location = %(here)s/alembic
file_template = %%(rev)s_%%(slug)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stdout,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`api/alembic/env.py`:
```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from shortener_api.settings import load_migrate_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    # Tests inject the URL via the Alembic config; the release job uses the environment.
    return config.get_main_option("sqlalchemy.url") or str(
        load_migrate_settings().migrator_database_url
    )


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), literal_binds=True, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`api/alembic/script.py.mako`:
```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 5: Write the failing migration integration tests**

`api/tests/integration/conftest.py`:
```python
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

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
        )
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


@pytest.fixture
def insert_link(migrated) -> Callable[..., UUID]:
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
        with migrated.connect("migrator") as conn:
            row = conn.execute(
                f"INSERT INTO public.links ({names}) VALUES ({params}) RETURNING id",  # noqa: S608 — test-only, column names are literals above
                values,
            ).fetchone()
        assert row is not None
        return UUID(str(row[0]))

    return _insert
```

`api/tests/integration/test_migrations.py`:
```python
from datetime import UTC, datetime

import psycopg
import pytest
from alembic import command
from psycopg import errors

pytestmark = pytest.mark.integration


def tables(conn: psycopg.Connection, schema: str) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)
    ).fetchall()
    return {r[0] for r in rows}


def test_all_tables_exist(migrated):
    with migrated.connect("migrator") as conn:
        assert {"links", "alembic_version"} <= tables(conn, "public")
        assert tables(conn, "analytics") == {
            "link_clicks_hourly",
            "link_referrers_daily",
            "pipeline_status",
        }
        assert tables(conn, "admin") == {"sessions"}


def test_pipeline_status_is_seeded_with_single_row(migrated):
    with migrated.connect("migrator") as conn:
        rows = conn.execute("SELECT id, last_committed_at FROM analytics.pipeline_status").fetchall()
    assert rows == [(1, None)]


def test_upgrade_is_idempotent(migrated, make_alembic_config):
    command.upgrade(make_alembic_config(migrated), "head")  # second run: no-op, no error


def test_code_is_unique(insert_link):
    insert_link(code="dupcode")
    with pytest.raises(errors.UniqueViolation):
        insert_link(code="dupcode")


def test_block_requires_by_and_reason(insert_link):
    with pytest.raises(errors.CheckViolation):
        insert_link(blocked_at=datetime.now(UTC))


def test_block_with_by_and_reason_is_allowed(insert_link):
    insert_link(blocked_at=datetime.now(UTC), blocked_by="sub-alice", blocked_reason="phishing")


def test_api_user_privileges(migrated, insert_link):
    link_id = insert_link()
    with migrated.connect("api_user") as conn:
        conn.execute("SELECT id, target_url FROM public.links WHERE id = %s", (link_id,))
        conn.execute("UPDATE public.links SET is_active = false WHERE id = %s", (link_id,))
        conn.execute("SELECT count(*) FROM analytics.link_clicks_hourly")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO analytics.link_clicks_hourly VALUES (%s, now(), 1)", (link_id,)
            )
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM admin.sessions")


def test_api_user_can_read_schema_version(migrated):
    with migrated.connect("api_user") as conn:
        assert conn.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("0001",)


def test_processor_user_privileges(migrated, insert_link):
    link_id = insert_link()
    with migrated.connect("processor_user") as conn:
        conn.execute("SELECT id, code FROM public.links WHERE id = %s", (link_id,))
        conn.execute(
            "INSERT INTO analytics.link_clicks_hourly VALUES (%s, date_trunc('hour', now()), 1)",
            (link_id,),
        )
        conn.execute("UPDATE analytics.pipeline_status SET last_committed_at = now() WHERE id = 1")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT target_url FROM public.links")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO public.links (code, target_url, owner_sub, owner_username) "
                "VALUES ('x1', 'https://x', 's', 'u')"
            )
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM admin.sessions")


def test_admin_user_is_confined_to_admin_schema(migrated):
    with migrated.connect("admin_user") as conn:
        conn.execute("SELECT count(*) FROM admin.sessions")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM public.links")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM analytics.link_clicks_hourly")


def test_deleting_a_link_cascades_to_rollups(migrated, insert_link):
    link_id = insert_link()
    with migrated.connect("migrator") as conn:
        conn.execute(
            "INSERT INTO analytics.link_clicks_hourly VALUES (%s, date_trunc('hour', now()), 3)",
            (link_id,),
        )
        conn.execute(
            "INSERT INTO analytics.link_referrers_daily VALUES (%s, current_date, '(direct)', 3)",
            (link_id,),
        )
    with migrated.connect("api_user") as conn:
        conn.execute("DELETE FROM public.links WHERE id = %s", (link_id,))
    with migrated.connect("migrator") as conn:
        hourly = conn.execute(
            "SELECT count(*) FROM analytics.link_clicks_hourly WHERE link_id = %s", (link_id,)
        ).fetchone()
        daily = conn.execute(
            "SELECT count(*) FROM analytics.link_referrers_daily WHERE link_id = %s", (link_id,)
        ).fetchone()
    assert hourly == (0,)
    assert daily == (0,)


def test_downgrade_and_upgrade_round_trip(pg_server, make_alembic_config):
    with pg_server.connect("postgres", db="postgres") as conn:
        conn.execute("CREATE DATABASE shortener_roundtrip OWNER migrator")
    cfg = make_alembic_config(pg_server, db="shortener_roundtrip")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with pg_server.connect("migrator", db="shortener_roundtrip") as conn:
        assert tables(conn, "admin") == {"sessions"}
```

If you're using colima, export these before running integration tests (add them to your shell profile; the README in Task 9 documents this):
```bash
export DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock"
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock
```

Run: `uv run pytest api/tests/integration -v`
Expected: FAIL. `command.upgrade` errors because `api/alembic/versions/` has no revisions, so the tables are missing.

- [ ] **Step 6: Write the initial migration**

`api/alembic/versions/0001_initial_schema.py`:
```python
"""initial schema: links, analytics rollups, admin sessions, grants

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)

GRANTS = [
    # api_user: read/write link data, read-only analytics (spec §3.4)
    "GRANT USAGE ON SCHEMA public TO api_user, processor_user",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON public.links TO api_user",
    "GRANT USAGE ON SCHEMA analytics TO api_user, processor_user",
    "GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO api_user",
    # processor_user: write analytics, read only (id, code) of links
    "GRANT SELECT (id, code) ON public.links TO processor_user",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics TO processor_user",
    # admin_user: admin schema only
    "GRANT USAGE ON SCHEMA admin TO admin_user",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON admin.sessions TO admin_user",
    # read-only visibility of the schema version (readiness/e2e checks); alembic creates
    # alembic_version before running this revision, so the grant succeeds
    "GRANT SELECT ON public.alembic_version TO api_user",
]


def upgrade() -> None:
    op.execute("CREATE SCHEMA analytics")
    op.execute("CREATE SCHEMA admin")

    op.create_table(
        "links",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("owner_sub", sa.String(64), nullable=False),
        sa.Column("owner_username", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("blocked_at", TZ, nullable=True),
        sa.Column("blocked_by", sa.String(64), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_links_code"),
        sa.CheckConstraint(
            "blocked_at IS NULL OR (blocked_by IS NOT NULL AND blocked_reason IS NOT NULL)",
            name="ck_links_block_fields",
        ),
        schema="public",
    )
    op.create_index("ix_links_owner_sub", "links", ["owner_sub"], schema="public")

    op.create_table(
        "link_clicks_hourly",
        sa.Column(
            "link_id",
            sa.Uuid(),
            sa.ForeignKey("public.links.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("bucket_start", TZ, primary_key=True),
        sa.Column("count", sa.BigInteger(), nullable=False),
        schema="analytics",
    )
    op.create_table(
        "link_referrers_daily",
        sa.Column(
            "link_id",
            sa.Uuid(),
            sa.ForeignKey("public.links.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("bucket_date", sa.Date(), primary_key=True),
        sa.Column("referrer_host", sa.Text(), primary_key=True),
        sa.Column("count", sa.BigInteger(), nullable=False),
        schema="analytics",
    )
    op.create_table(
        "pipeline_status",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("last_committed_at", TZ, nullable=True),
        sa.CheckConstraint("id = 1", name="ck_pipeline_status_single_row"),
        schema="analytics",
    )
    op.execute("INSERT INTO analytics.pipeline_status (id) VALUES (1)")

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("sub", sa.String(64), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("id_token", sa.Text(), nullable=False),
        sa.Column("access_expires_at", TZ, nullable=False),
        sa.Column("refresh_expires_at", TZ, nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", TZ, nullable=False, server_default=sa.func.now()),
        schema="admin",
    )

    for statement in GRANTS:
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("sessions", schema="admin")
    op.drop_table("pipeline_status", schema="analytics")
    op.drop_table("link_referrers_daily", schema="analytics")
    op.drop_table("link_clicks_hourly", schema="analytics")
    op.drop_index("ix_links_owner_sub", table_name="links", schema="public")
    op.drop_table("links", schema="public")
    op.execute("DROP SCHEMA admin")
    op.execute("DROP SCHEMA analytics")
```

- [ ] **Step 7: Run the integration tests and confirm they pass**

Run: `uv run pytest api/tests -v`
Expected: all PASS (unit + integration).

- [ ] **Step 8: Run the quality gates**

Run: `make lint typecheck`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add api pyproject.toml uv.lock Makefile
git commit -m "feat(api): settings, alembic, initial schema with per-service grants

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: API image and the `migrate` release job

**Files:**
- Create: `api/Dockerfile`
- Modify: `docker-compose.yml` (add `migrate`)
- Test: `tests/e2e/conftest.py`, `tests/e2e/__init__.py` (empty), `tests/e2e/test_database.py`

**Interfaces:**
- Consumes: Alembic revision `0001` (Task 4).
- Produces: image `shortener-api:${GIT_SHA}`, whose venv lives at `/app/.venv` (on `PATH`) with Alembic files at `/app`. Env `SERVICE_VERSION=$GIT_SHA`. Compose service `migrate`. The e2e settings fixture `e2e_settings` (`E2ESettings`) is used by Tasks 6–9.

- [ ] **Step 1: Write the e2e fixtures and the failing database test**

`tests/e2e/conftest.py`:
```python
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class E2ESettings(BaseSettings):
    """Reads the same .env that docker compose uses, plus host-side URLs."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    api_db_password: str
    admin_db_password: str
    keycloak_url: str = "http://localhost:8080"
    keycloak_realm: str = "shortener"
    keycloak_admin_user: str
    keycloak_admin_password: str
    shortener_admin_client_secret: str
    sqs_endpoint_url: str = "http://localhost:9324"
    grafana_url: str = "http://localhost:3000"
    otlp_http_url: str = "http://localhost:4318"


@pytest.fixture(scope="session")
def e2e_settings() -> E2ESettings:
    return E2ESettings()  # type: ignore[call-arg]  # values come from .env / environment
```

`tests/e2e/test_database.py`:
```python
import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.e2e


def connect(settings, user: str, password: str) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=user,
        password=password,
        dbname="shortener",
        autocommit=True,
    )


def test_migrations_applied_and_api_user_can_read_links(e2e_settings):
    with connect(e2e_settings, "api_user", e2e_settings.api_db_password) as conn:
        version = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
        conn.execute("SELECT count(*) FROM public.links")
    assert version == ("0001",)


def test_admin_user_cannot_read_links(e2e_settings):
    with connect(e2e_settings, "admin_user", e2e_settings.admin_db_password) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM public.links")
```
Run: `docker compose down -v && docker compose up -d --wait postgres && uv run pytest -m e2e tests/e2e/test_database.py -v`
Expected: FAIL with `UndefinedTable: relation "public.alembic_version" does not exist` (no migration has run yet).

- [ ] **Step 2: Write the API Dockerfile**

`api/Dockerfile`:
```dockerfile
# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package shortener-api

FROM python:3.12-slim
ARG GIT_SHA=dev
ENV PATH=/app/.venv/bin:$PATH SERVICE_VERSION=$GIT_SHA PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
COPY api/alembic.ini /app/alembic.ini
COPY api/alembic /app/alembic
WORKDIR /app
USER 10001
```

- [ ] **Step 3: Add the `migrate` job to compose**

Add under `services:` in `docker-compose.yml`:
```yaml
  migrate:
    build:
      context: .
      dockerfile: api/Dockerfile
      args:
        GIT_SHA: ${GIT_SHA:-dev}
    image: shortener-api:${GIT_SHA:-dev}
    command: ["alembic", "upgrade", "head"]
    environment:
      MIGRATOR_DATABASE_URL: postgresql+psycopg://migrator:${MIGRATOR_DB_PASSWORD}@postgres:5432/shortener
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"
```

- [ ] **Step 4: Run the migration job and the e2e test**

Run:
```bash
make up
docker compose run --rm migrate   # second run must be a no-op
uv run pytest -m e2e tests/e2e/test_database.py -v
```
Expected: the first run logs `Running upgrade  -> 0001`; the second run logs nothing about upgrades. Both e2e tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/Dockerfile docker-compose.yml tests/e2e
git commit -m "feat(infra): api image and migrate release job

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: ElasticMQ queues

**Files:**
- Create: `infra/elasticmq/elasticmq.conf`
- Modify: `docker-compose.yml` (add `elasticmq`), `Makefile` (`up`)
- Test: `tests/e2e/test_queues.py`

**Interfaces:**
- Produces: the SQS API at `http://localhost:9324` (host) / `http://elasticmq:9324` (compose network); region `us-east-1`, account `000000000000`; queues `click-events` and `click-events-dlq`. The stats UI is at `http://localhost:9325`.

- [ ] **Step 1: Write the failing queue test**

`tests/e2e/test_queues.py`:
```python
import json

import boto3
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def sqs(e2e_settings):
    return boto3.client(
        "sqs",
        endpoint_url=e2e_settings.sqs_endpoint_url,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )


def test_click_events_queue_configuration(sqs):
    url = sqs.get_queue_url(QueueName="click-events")["QueueUrl"]
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    assert attrs["VisibilityTimeout"] == "30"
    assert attrs["ReceiveMessageWaitTimeSeconds"] == "20"
    redrive = json.loads(attrs["RedrivePolicy"])
    assert int(redrive["maxReceiveCount"]) == 5
    assert redrive["deadLetterTargetArn"].endswith(":click-events-dlq")


def test_dlq_exists(sqs):
    assert sqs.get_queue_url(QueueName="click-events-dlq")["QueueUrl"]


def test_message_round_trip_with_attributes(sqs):
    url = sqs.get_queue_url(QueueName="click-events")["QueueUrl"]
    sqs.purge_queue(QueueUrl=url)
    sqs.send_message(
        QueueUrl=url,
        MessageBody="{}",
        MessageAttributes={"type": {"DataType": "String", "StringValue": "e2e.ping"}},
    )
    received = sqs.receive_message(
        QueueUrl=url, MessageAttributeNames=["All"], WaitTimeSeconds=5
    )["Messages"]
    assert received[0]["MessageAttributes"]["type"]["StringValue"] == "e2e.ping"
    sqs.delete_message(QueueUrl=url, ReceiptHandle=received[0]["ReceiptHandle"])
```

Run: `uv run pytest -m e2e tests/e2e/test_queues.py -v`
Expected: FAIL with `EndpointConnectionError` (nothing is listening on 9324).

- [ ] **Step 2: Write the ElasticMQ config**

`infra/elasticmq/elasticmq.conf`:
```hocon
include classpath("application.conf")

# "*" = build queue URLs from the request Host header, so they work from the host and the compose network.
node-address {
  protocol = http
  host = "*"
  port = 9324
  context-path = ""
}

rest-sqs {
  enabled = true
  bind-port = 9324
  bind-hostname = "0.0.0.0"
  sqs-limits = strict
}

rest-stats {
  enabled = true
  bind-port = 9325
  bind-hostname = "0.0.0.0"
}

aws {
  region = us-east-1
  accountId = 000000000000
}

queues {
  click-events {
    defaultVisibilityTimeout = 30 seconds
    receiveMessageWait = 20 seconds
    deadLettersQueue {
      name = "click-events-dlq"
      maxReceiveCount = 5
    }
  }
  click-events-dlq {}
}
```

- [ ] **Step 3: Add the service and extend `make up`**

Add to `docker-compose.yml` under `services:`:
```yaml
  elasticmq:
    image: softwaremill/elasticmq-native:1.6.14
    ports:
      - "9324:9324"
      - "9325:9325"
    volumes:
      - ./infra/elasticmq/elasticmq.conf:/opt/elasticmq.conf:ro
    # No healthcheck: the native image ships no shell or HTTP client. Consumers retry on connect.
```
In the `Makefile`, change the first line of `up` to:
```make
	$(COMPOSE) up -d --build --wait postgres elasticmq
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `make up && uv run pytest -m e2e tests/e2e/test_queues.py -v`
Expected: all PASS. If `ReceiveMessageWaitTimeSeconds` comes back as `0`, your ElasticMQ version names the setting differently. Check its README for the queue-level long-poll key, update the conf, and re-run. Don't relax the test.

- [ ] **Step 5: Commit**

```bash
git add infra/elasticmq docker-compose.yml Makefile tests/e2e/test_queues.py
git commit -m "feat(infra): elasticmq with click-events queue and DLQ redrive

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: Keycloak with the `shortener` realm

**Files:**
- Create: `infra/keycloak/realm-export.json`
- Modify: `docker-compose.yml` (add `keycloak`), `Makefile` (`up`)
- Test: `tests/e2e/test_realm.py`

**Interfaces:**
- Produces:
  - Keycloak at `http://localhost:8080` (host and browser) / `http://keycloak:8080` (compose network), with issuer `http://localhost:8080/realms/shortener`.
  - Realm roles `admin`, `editor`, `viewer`.
  - Clients `shortener-admin` (confidential, standard flow, PKCE S256, secret `${SHORTENER_ADMIN_CLIENT_SECRET}`), `shortener-api` (no flows), and `shortener-dev` (public, direct access grants). `shortener-admin` and `shortener-dev` carry an audience mapper that adds `shortener-api`.
  - Bootstrap admin credentials `KEYCLOAK_ADMIN_USER` / `KEYCLOAK_ADMIN_PASSWORD` (master realm, `admin-cli`).

- [ ] **Step 1: Write the failing realm test**

`tests/e2e/test_realm.py`:
```python
import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def admin_http(e2e_settings):
    token = httpx.post(
        f"{e2e_settings.keycloak_url}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": e2e_settings.keycloak_admin_user,
            "password": e2e_settings.keycloak_admin_password,
        },
        timeout=10,
    ).raise_for_status().json()["access_token"]
    base = f"{e2e_settings.keycloak_url}/admin/realms/{e2e_settings.keycloak_realm}"
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=10) as c:
        yield c


def clients_by_id(admin_http) -> dict[str, dict]:
    return {c["clientId"]: c for c in admin_http.get("/clients").raise_for_status().json()}


def test_issuer_is_the_browser_facing_url(e2e_settings):
    url = f"{e2e_settings.keycloak_url}/realms/shortener/.well-known/openid-configuration"
    discovery = httpx.get(url, timeout=10).raise_for_status().json()
    assert discovery["issuer"] == "http://localhost:8080/realms/shortener"


def test_realm_roles_exist(admin_http):
    names = {r["name"] for r in admin_http.get("/roles").raise_for_status().json()}
    assert {"admin", "editor", "viewer"} <= names


def test_admin_client_is_confidential_with_pkce(admin_http):
    client = clients_by_id(admin_http)["shortener-admin"]
    assert client["publicClient"] is False
    assert client["standardFlowEnabled"] is True
    assert client["directAccessGrantsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert "http://localhost:8001/auth/callback" in client["redirectUris"]


def test_api_client_has_no_flows(admin_http):
    client = clients_by_id(admin_http)["shortener-api"]
    assert not client["standardFlowEnabled"]
    assert not client["directAccessGrantsEnabled"]
    assert not client["serviceAccountsEnabled"]


def test_dev_client_is_public_with_password_grant(admin_http):
    client = clients_by_id(admin_http)["shortener-dev"]
    assert client["publicClient"] is True
    assert client["directAccessGrantsEnabled"] is True
    assert client["standardFlowEnabled"] is False


def test_admin_client_secret_comes_from_env(admin_http, e2e_settings):
    client = clients_by_id(admin_http)["shortener-admin"]
    secret = admin_http.get(f"/clients/{client['id']}/client-secret").raise_for_status().json()
    assert secret["value"] == e2e_settings.shortener_admin_client_secret


@pytest.mark.parametrize("client_id", ["shortener-admin", "shortener-dev"])
def test_audience_mapper_adds_shortener_api(admin_http, client_id):
    mappers = clients_by_id(admin_http)[client_id].get("protocolMappers", [])
    audiences = {
        m["config"].get("included.client.audience")
        for m in mappers
        if m["protocolMapper"] == "oidc-audience-mapper"
    }
    assert "shortener-api" in audiences
```

Run: `uv run pytest -m e2e tests/e2e/test_realm.py -v`
Expected: FAIL with `ConnectError` (Keycloak isn't running).

- [ ] **Step 2: Write the realm import**

`infra/keycloak/realm-export.json`:
```json
{
  "realm": "shortener",
  "enabled": true,
  "sslRequired": "external",
  "registrationAllowed": false,
  "loginWithEmailAllowed": false,
  "duplicateEmailsAllowed": false,
  "accessTokenLifespan": 300,
  "ssoSessionIdleTimeout": 1800,
  "roles": {
    "realm": [
      { "name": "admin", "description": "Manage all links; block/unblock" },
      { "name": "editor", "description": "Create and manage own links" },
      { "name": "viewer", "description": "Read-only access to all links and stats" }
    ]
  },
  "clients": [
    {
      "clientId": "shortener-admin",
      "name": "URL Shortener Admin UI",
      "enabled": true,
      "protocol": "openid-connect",
      "publicClient": false,
      "clientAuthenticatorType": "client-secret",
      "secret": "${SHORTENER_ADMIN_CLIENT_SECRET}",
      "standardFlowEnabled": true,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false,
      "redirectUris": ["http://localhost:8001/auth/callback"],
      "webOrigins": ["http://localhost:8001"],
      "attributes": {
        "pkce.code.challenge.method": "S256",
        "post.logout.redirect.uris": "http://localhost:8001/"
      },
      "defaultClientScopes": ["basic", "profile", "email", "roles", "web-origins", "acr"],
      "protocolMappers": [
        {
          "name": "audience-shortener-api",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "config": {
            "included.client.audience": "shortener-api",
            "access.token.claim": "true",
            "id.token.claim": "false",
            "introspection.token.claim": "true"
          }
        }
      ]
    },
    {
      "clientId": "shortener-api",
      "name": "URL Shortener API (resource server)",
      "enabled": true,
      "protocol": "openid-connect",
      "publicClient": false,
      "standardFlowEnabled": false,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false
    },
    {
      "clientId": "shortener-dev",
      "name": "DEV ONLY - password grant for curl and e2e tests",
      "enabled": true,
      "protocol": "openid-connect",
      "publicClient": true,
      "standardFlowEnabled": false,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": true,
      "serviceAccountsEnabled": false,
      "defaultClientScopes": ["basic", "profile", "email", "roles", "web-origins", "acr"],
      "protocolMappers": [
        {
          "name": "audience-shortener-api",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "config": {
            "included.client.audience": "shortener-api",
            "access.token.claim": "true",
            "id.token.claim": "false",
            "introspection.token.claim": "true"
          }
        }
      ]
    }
  ]
}
```
Why `basic` is listed explicitly: since Keycloak 25, the `sub` claim comes from the `basic` client scope. A client without it issues access tokens with no `sub`. Task 8's token test checks for `sub`.

- [ ] **Step 3: Add the Keycloak service and extend `make up`**

Add to `docker-compose.yml` under `services:`:
```yaml
  keycloak:
    image: quay.io/keycloak/keycloak:26.4.0
    command: ["start-dev", "--import-realm"]
    environment:
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak
      KC_DB_USERNAME: keycloak
      KC_DB_PASSWORD: ${KEYCLOAK_DB_PASSWORD}
      KC_HOSTNAME: http://localhost:8080
      KC_HOSTNAME_BACKCHANNEL_DYNAMIC: "true"
      KC_HEALTH_ENABLED: "true"
      KC_BOOTSTRAP_ADMIN_USERNAME: ${KEYCLOAK_ADMIN_USER}
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
      SHORTENER_ADMIN_CLIENT_SECRET: ${SHORTENER_ADMIN_CLIENT_SECRET}
    ports:
      - "8080:8080"
    volumes:
      - ./infra/keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      # The image has bash but no curl; probe the management port's readiness endpoint over /dev/tcp.
      test:
        - CMD-SHELL
        - >-
          exec 3<>/dev/tcp/127.0.0.1/9000 &&
          printf 'GET /health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' >&3 &&
          grep -Eq '"status" ?: ?"UP"' <&3
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 30s
```
Notes:
- `--import-realm` skips a realm that already exists, so restarts are idempotent. A changed `realm-export.json` takes effect only after `make down` (volumes removed).
- `start-dev` is used locally only. Production (AWS) uses an optimized `start` build; that's out of scope for this plan.

In the `Makefile`, change the first line of `up` to:
```make
	$(COMPOSE) up -d --build --wait postgres elasticmq keycloak
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `make up && uv run pytest -m e2e tests/e2e/test_realm.py -v`
Expected: all PASS. If `test_admin_client_secret_comes_from_env` fails with the literal string `${SHORTENER_ADMIN_CLIENT_SECRET}`, this Keycloak version isn't substituting environment placeholders in realm imports. Stop and report it as a blocker; don't hard-code the secret in the realm file.

- [ ] **Step 5: Commit**

```bash
git add infra/keycloak/realm-export.json docker-compose.yml Makefile tests/e2e/test_realm.py
git commit -m "feat(infra): keycloak with shortener realm, roles, and clients

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Idempotent user seeding (`keycloak-tools`)

**Files:**
- Create: `tools/keycloak-tools/pyproject.toml`, `tools/keycloak-tools/Dockerfile`
- Create: `tools/keycloak-tools/src/keycloak_tools/{__init__,users,plan,admin_client,seed}.py`, `py.typed`
- Create: `infra/keycloak/users.yaml`
- Modify: `pyproject.toml` (members), `Makefile` (`MYPY_TARGETS`, `up`), `docker-compose.yml` (add `keycloak-seed`)
- Test: `tools/keycloak-tools/tests/{test_users,test_plan,test_seed,test_admin_client}.py`, `tests/e2e/test_seed.py`

**Interfaces:**
- Consumes: the realm from Task 7.
- Produces:
  - `keycloak_tools.users.MANAGED_ROLES: frozenset[str] = frozenset({"admin", "editor", "viewer"})`
  - `keycloak_tools.users.DesiredUser`: frozen pydantic model with `username`, `email`, `first_name`, `last_name`, `password` (hidden from repr), `roles: frozenset[str]`
  - `keycloak_tools.users.load_users(path: Path) -> list[DesiredUser]`
  - `keycloak_tools.plan.ExistingUser(id, username, email, first_name, last_name, enabled, roles)`
  - Action dataclasses `CreateUser(user)`, `UpdateProfile(user_id, user)`, `SetPassword(user_id, username, password)`, `AddRoles(user_id, username, roles)`, `RemoveRoles(user_id, username, roles)`, and the alias `Action`
  - `keycloak_tools.plan.plan_changes(desired: Sequence[DesiredUser], existing: Sequence[ExistingUser], reset_passwords: bool = False) -> list[Action]`
  - `keycloak_tools.admin_client.KeycloakAdmin` (Protocol) and `KeycloakAdminClient` (httpx)
  - `keycloak_tools.seed.run_seed(admin: KeycloakAdmin, desired: Sequence[DesiredUser], reset_passwords: bool = False) -> list[Action]` and `keycloak_tools.seed.main()`

Seeding rules:
- Users missing from Keycloak are created, with their password and roles.
- Existing users get their profile corrected (email, names, enabled = true) and their **managed** roles reconciled.
- Passwords are set only at creation, unless `RESET_PASSWORDS=true`.
- Roles outside `MANAGED_ROLES` are never removed.
- Users not in the file are left alone.

- [ ] **Step 1: Create the package and register it**

`tools/keycloak-tools/pyproject.toml`:
```toml
[project]
name = "keycloak-tools"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "pyyaml>=6.0",
]

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/keycloak_tools"]
```
Create `tools/keycloak-tools/src/keycloak_tools/__init__.py` with `"""Dev tooling for the local Keycloak realm (seeding users, fetching tokens)."""` and an empty `py.typed`.

Root `pyproject.toml`: `members = ["libs/shortener-events", "api", "tools/keycloak-tools"]`.
`Makefile`: `MYPY_TARGETS := libs/shortener-events/src api/src tools/keycloak-tools/src`.

Run: `uv lock && uv sync --all-packages`
Expected: success.

- [ ] **Step 2: Write the failing user-file tests**

`tools/keycloak-tools/tests/test_users.py`:
```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from keycloak_tools.users import load_users

REPO_ROOT = Path(__file__).resolve().parents[3]

VALID = """
users:
  - username: alice
    email: alice@example.test
    first_name: Alice
    last_name: Admin
    password: s3cret
    roles: [admin]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "users.yaml"
    path.write_text(text)
    return path


def test_loads_valid_file(tmp_path):
    [alice] = load_users(write(tmp_path, VALID))
    assert alice.username == "alice"
    assert alice.roles == frozenset({"admin"})


def test_password_is_hidden_from_repr(tmp_path):
    [alice] = load_users(write(tmp_path, VALID))
    assert "s3cret" not in repr(alice)


def test_unknown_role_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="unknown role"):
        load_users(write(tmp_path, VALID.replace("[admin]", "[superuser]")))


def test_duplicate_usernames_are_rejected(tmp_path):
    body = VALID + VALID.split("users:\n", 1)[1]
    with pytest.raises(ValidationError, match="duplicate username"):
        load_users(write(tmp_path, body))


def test_uppercase_username_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_users(write(tmp_path, VALID.replace("username: alice", "username: Alice")))


def test_user_without_roles_is_allowed(tmp_path):
    [user] = load_users(write(tmp_path, VALID.replace("roles: [admin]", "roles: []")))
    assert user.roles == frozenset()


def test_repo_users_file_defines_the_demo_users():
    users = {u.username: u.roles for u in load_users(REPO_ROOT / "infra/keycloak/users.yaml")}
    assert users == {
        "alice": frozenset({"admin"}),
        "eddie": frozenset({"editor"}),
        "erin": frozenset({"editor"}),
        "victor": frozenset({"viewer"}),
        "nora": frozenset(),
    }
```

Run: `uv run pytest tools/keycloak-tools/tests/test_users.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'keycloak_tools.users'`.

- [ ] **Step 3: Implement `users.py` and write `users.yaml`**

`tools/keycloak-tools/src/keycloak_tools/users.py`:
```python
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MANAGED_ROLES: frozenset[str] = frozenset({"admin", "editor", "viewer"})


class DesiredUser(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    username: str = Field(pattern=r"^[a-z0-9._-]{3,64}$")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    password: str = Field(min_length=1, repr=False)
    roles: frozenset[str] = frozenset()

    @field_validator("roles")
    @classmethod
    def _known_roles(cls, roles: frozenset[str]) -> frozenset[str]:
        unknown = roles - MANAGED_ROLES
        if unknown:
            raise ValueError(f"unknown role(s): {sorted(unknown)}")
        return roles


class UsersFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[DesiredUser]

    @model_validator(mode="after")
    def _unique_usernames(self) -> "UsersFile":
        seen: set[str] = set()
        for user in self.users:
            if user.username in seen:
                raise ValueError(f"duplicate username: {user.username}")
            seen.add(user.username)
        return self


def load_users(path: Path) -> list[DesiredUser]:
    return UsersFile.model_validate(yaml.safe_load(path.read_text())).users
```

`infra/keycloak/users.yaml`:
```yaml
# DEV ONLY — seeded into the local `shortener` realm by `make up` / `make seed-users`.
# Passwords are set on creation only (set RESET_PASSWORDS=true to force).
users:
  - username: alice
    email: alice@example.test
    first_name: Alice
    last_name: Admin
    password: password
    roles: [admin]
  - username: eddie
    email: eddie@example.test
    first_name: Eddie
    last_name: Editor
    password: password
    roles: [editor]
  - username: erin
    email: erin@example.test
    first_name: Erin
    last_name: Editor
    password: password
    roles: [editor]
  - username: victor
    email: victor@example.test
    first_name: Victor
    last_name: Viewer
    password: password
    roles: [viewer]
  - username: nora
    email: nora@example.test
    first_name: Nora
    last_name: Noroles
    password: password
    roles: []
```
Every user has an email and both names. Keycloak 24+ treats these as required profile fields, and a user missing them gets "Account is not fully set up" on the password grant.

Run: `uv run pytest tools/keycloak-tools/tests/test_users.py -v`
Expected: all PASS.

- [ ] **Step 4: Write the failing planner tests**

`tools/keycloak-tools/tests/test_plan.py`:
```python
from keycloak_tools.plan import (
    AddRoles,
    CreateUser,
    ExistingUser,
    RemoveRoles,
    SetPassword,
    UpdateProfile,
    plan_changes,
)
from keycloak_tools.users import DesiredUser


def desired(username="eddie", roles=("editor",), **kw) -> DesiredUser:
    fields = {
        "username": username,
        "email": f"{username}@example.test",
        "first_name": username.title(),
        "last_name": "Test",
        "password": "pw",
        "roles": frozenset(roles),
    }
    fields.update(kw)
    return DesiredUser(**fields)


def existing(user: DesiredUser, uid="id-1", roles=None, enabled=True, **kw) -> ExistingUser:
    fields = {
        "id": uid,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "enabled": enabled,
        "roles": frozenset(user.roles if roles is None else roles),
    }
    fields.update(kw)
    return ExistingUser(**fields)


def test_missing_users_are_created():
    eddie, nora = desired(), desired("nora", roles=())
    assert plan_changes([eddie, nora], []) == [CreateUser(eddie), CreateUser(nora)]


def test_identical_state_produces_no_actions():
    eddie = desired()
    assert plan_changes([eddie], [existing(eddie)]) == []


def test_profile_drift_is_corrected():
    eddie = desired()
    assert plan_changes([eddie], [existing(eddie, email="old@example.test")]) == [
        UpdateProfile("id-1", eddie)
    ]


def test_disabled_user_is_re_enabled():
    eddie = desired()
    assert plan_changes([eddie], [existing(eddie, enabled=False)]) == [UpdateProfile("id-1", eddie)]


def test_managed_roles_are_reconciled():
    eddie = desired(roles=("editor",))
    actions = plan_changes([eddie], [existing(eddie, roles={"viewer"})])
    assert actions == [
        AddRoles("id-1", "eddie", frozenset({"editor"})),
        RemoveRoles("id-1", "eddie", frozenset({"viewer"})),
    ]


def test_unmanaged_roles_are_never_removed():
    nora = desired("nora", roles=())
    current = {"default-roles-shortener", "offline_access", "uma_authorization"}
    assert plan_changes([nora], [existing(nora, roles=current)]) == []


def test_passwords_are_only_reset_when_asked():
    eddie = desired()
    assert plan_changes([eddie], [existing(eddie)], reset_passwords=False) == []
    assert plan_changes([eddie], [existing(eddie)], reset_passwords=True) == [
        SetPassword("id-1", "eddie", "pw")
    ]


def test_users_not_in_the_file_are_left_alone():
    eddie, stranger = desired(), desired("stranger")
    assert plan_changes([eddie], [existing(eddie), existing(stranger, uid="id-2")]) == []


def test_set_password_repr_hides_the_password():
    assert "pw" not in repr(SetPassword("id-1", "eddie", "pw")).replace("eddie", "")
```

Run: `uv run pytest tools/keycloak-tools/tests/test_plan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'keycloak_tools.plan'`.

- [ ] **Step 5: Implement the planner**

`tools/keycloak-tools/src/keycloak_tools/plan.py`:
```python
from collections.abc import Sequence
from dataclasses import dataclass, field

from keycloak_tools.users import MANAGED_ROLES, DesiredUser


@dataclass(frozen=True)
class ExistingUser:
    id: str
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None
    enabled: bool
    roles: frozenset[str]


@dataclass(frozen=True)
class CreateUser:
    user: DesiredUser


@dataclass(frozen=True)
class UpdateProfile:
    user_id: str
    user: DesiredUser


@dataclass(frozen=True)
class SetPassword:
    user_id: str
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class AddRoles:
    user_id: str
    username: str
    roles: frozenset[str]


@dataclass(frozen=True)
class RemoveRoles:
    user_id: str
    username: str
    roles: frozenset[str]


Action = CreateUser | UpdateProfile | SetPassword | AddRoles | RemoveRoles


def _profile_differs(want: DesiredUser, have: ExistingUser) -> bool:
    return (
        not have.enabled
        or have.email != want.email
        or have.first_name != want.first_name
        or have.last_name != want.last_name
    )


def plan_changes(
    desired: Sequence[DesiredUser],
    existing: Sequence[ExistingUser],
    reset_passwords: bool = False,
) -> list[Action]:
    """Compute the actions that make Keycloak match `desired`. Pure; never deletes users."""
    by_username = {user.username: user for user in existing}
    actions: list[Action] = []
    for want in desired:
        have = by_username.get(want.username)
        if have is None:
            actions.append(CreateUser(want))
            continue
        if _profile_differs(want, have):
            actions.append(UpdateProfile(have.id, want))
        if reset_passwords:
            actions.append(SetPassword(have.id, want.username, want.password))
        current_managed = have.roles & MANAGED_ROLES
        if to_add := want.roles - current_managed:
            actions.append(AddRoles(have.id, want.username, frozenset(to_add)))
        if to_remove := current_managed - want.roles:
            actions.append(RemoveRoles(have.id, want.username, frozenset(to_remove)))
    return actions
```

Run: `uv run pytest tools/keycloak-tools/tests/test_plan.py -v`
Expected: all PASS.

- [ ] **Step 6: Write the failing seed tests (with a hand-written fake)**

`tools/keycloak-tools/tests/test_seed.py`:
```python
from keycloak_tools.plan import CreateUser, ExistingUser
from keycloak_tools.seed import run_seed
from keycloak_tools.users import DesiredUser


class FakeKeycloakAdmin:
    def __init__(self) -> None:
        self.users: dict[str, ExistingUser] = {}
        self.passwords: dict[str, str] = {}

    def list_users(self) -> list[ExistingUser]:
        return list(self.users.values())

    def create_user(self, user: DesiredUser) -> str:
        uid = f"id-{len(self.users) + 1}"
        self.users[uid] = ExistingUser(
            uid, user.username, user.email, user.first_name, user.last_name, True, frozenset()
        )
        self.passwords[uid] = user.password
        return uid

    def update_profile(self, user_id: str, user: DesiredUser) -> None:
        old = self.users[user_id]
        self.users[user_id] = ExistingUser(
            user_id, old.username, user.email, user.first_name, user.last_name, True, old.roles
        )

    def set_password(self, user_id: str, password: str) -> None:
        self.passwords[user_id] = password

    def add_realm_roles(self, user_id: str, roles: frozenset[str]) -> None:
        old = self.users[user_id]
        self.users[user_id] = ExistingUser(**{**old.__dict__, "roles": old.roles | roles})

    def remove_realm_roles(self, user_id: str, roles: frozenset[str]) -> None:
        old = self.users[user_id]
        self.users[user_id] = ExistingUser(**{**old.__dict__, "roles": old.roles - roles})


ALICE = DesiredUser(
    username="alice", email="alice@example.test", first_name="Alice", last_name="Admin",
    password="pw", roles=frozenset({"admin"}),
)


def test_first_run_creates_user_with_password_and_roles():
    admin = FakeKeycloakAdmin()
    assert run_seed(admin, [ALICE]) == [CreateUser(ALICE)]
    [created] = admin.users.values()
    assert created.roles == frozenset({"admin"})
    assert admin.passwords[created.id] == "pw"


def test_second_run_makes_no_changes():
    admin = FakeKeycloakAdmin()
    run_seed(admin, [ALICE])
    assert run_seed(admin, [ALICE]) == []


def test_role_drift_is_repaired():
    admin = FakeKeycloakAdmin()
    run_seed(admin, [ALICE])
    uid = next(iter(admin.users))
    admin.remove_realm_roles(uid, frozenset({"admin"}))
    admin.add_realm_roles(uid, frozenset({"viewer"}))
    run_seed(admin, [ALICE])
    assert admin.users[uid].roles == frozenset({"admin"})


def test_reset_passwords_overwrites_existing_password():
    admin = FakeKeycloakAdmin()
    run_seed(admin, [ALICE])
    uid = next(iter(admin.users))
    admin.passwords[uid] = "changed-by-user"
    run_seed(admin, [ALICE], reset_passwords=True)
    assert admin.passwords[uid] == "pw"
```

Run: `uv run pytest tools/keycloak-tools/tests/test_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'keycloak_tools.seed'`.

- [ ] **Step 7: Implement the admin protocol, the HTTP client, and `seed.py`**

`tools/keycloak-tools/src/keycloak_tools/admin_client.py`:
```python
from typing import Any, Protocol

import httpx

from keycloak_tools.plan import ExistingUser
from keycloak_tools.users import DesiredUser


class KeycloakAdmin(Protocol):
    def list_users(self) -> list[ExistingUser]: ...
    def create_user(self, user: DesiredUser) -> str: ...
    def update_profile(self, user_id: str, user: DesiredUser) -> None: ...
    def set_password(self, user_id: str, password: str) -> None: ...
    def add_realm_roles(self, user_id: str, roles: frozenset[str]) -> None: ...
    def remove_realm_roles(self, user_id: str, roles: frozenset[str]) -> None: ...


class KeycloakAdminClient:
    """Keycloak admin REST client authenticated as the master-realm bootstrap admin."""

    def __init__(
        self,
        base_url: str,
        realm: str,
        admin_user: str,
        admin_password: str,
        http: httpx.Client | None = None,
    ) -> None:
        self._http = http or httpx.Client(base_url=base_url, timeout=10.0)
        self._realm = realm
        response = self._http.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": admin_user,
                "password": admin_password,
            },
        )
        response.raise_for_status()
        self._http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    def _path(self, suffix: str) -> str:
        return f"/admin/realms/{self._realm}{suffix}"

    def _get(self, suffix: str, **params: Any) -> Any:
        response = self._http.get(self._path(suffix), params=params)
        response.raise_for_status()
        return response.json()

    def list_users(self) -> list[ExistingUser]:
        users = []
        for raw in self._get("/users", max=1000, briefRepresentation="false"):
            mappings = self._get(f"/users/{raw['id']}/role-mappings/realm")
            users.append(
                ExistingUser(
                    id=str(raw["id"]),
                    username=str(raw["username"]),
                    email=raw.get("email"),
                    first_name=raw.get("firstName"),
                    last_name=raw.get("lastName"),
                    enabled=bool(raw.get("enabled", False)),
                    roles=frozenset(str(m["name"]) for m in mappings),
                )
            )
        return users

    def _profile(self, user: DesiredUser) -> dict[str, Any]:
        return {
            "username": user.username,
            "email": user.email,
            "firstName": user.first_name,
            "lastName": user.last_name,
            "enabled": True,
            "emailVerified": True,
        }

    def create_user(self, user: DesiredUser) -> str:
        body = self._profile(user) | {
            "credentials": [{"type": "password", "value": user.password, "temporary": False}]
        }
        response = self._http.post(self._path("/users"), json=body)
        response.raise_for_status()
        return response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]

    def update_profile(self, user_id: str, user: DesiredUser) -> None:
        self._http.put(self._path(f"/users/{user_id}"), json=self._profile(user)).raise_for_status()

    def set_password(self, user_id: str, password: str) -> None:
        self._http.put(
            self._path(f"/users/{user_id}/reset-password"),
            json={"type": "password", "value": password, "temporary": False},
        ).raise_for_status()

    def _role_representations(self, roles: frozenset[str]) -> list[dict[str, Any]]:
        return [dict(self._get(f"/roles/{name}")) for name in sorted(roles)]

    def add_realm_roles(self, user_id: str, roles: frozenset[str]) -> None:
        self._http.post(
            self._path(f"/users/{user_id}/role-mappings/realm"),
            json=self._role_representations(roles),
        ).raise_for_status()

    def remove_realm_roles(self, user_id: str, roles: frozenset[str]) -> None:
        self._http.request(
            "DELETE",
            self._path(f"/users/{user_id}/role-mappings/realm"),
            json=self._role_representations(roles),
        ).raise_for_status()
```

`tools/keycloak-tools/src/keycloak_tools/seed.py`:
```python
"""Seed realm users from a YAML file. Idempotent: a second run reports no changes."""

from collections.abc import Sequence
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from keycloak_tools.admin_client import KeycloakAdmin, KeycloakAdminClient
from keycloak_tools.plan import (
    Action,
    AddRoles,
    CreateUser,
    RemoveRoles,
    SetPassword,
    UpdateProfile,
    plan_changes,
)
from keycloak_tools.users import DesiredUser, load_users


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    keycloak_url: str = "http://localhost:8080"
    keycloak_realm: str = "shortener"
    keycloak_admin_user: str
    keycloak_admin_password: str
    users_file: Path = Path("infra/keycloak/users.yaml")
    reset_passwords: bool = False


def run_seed(
    admin: KeycloakAdmin, desired: Sequence[DesiredUser], reset_passwords: bool = False
) -> list[Action]:
    actions = plan_changes(desired, admin.list_users(), reset_passwords=reset_passwords)
    for action in actions:
        match action:
            case CreateUser(user=user):
                user_id = admin.create_user(user)
                if user.roles:
                    admin.add_realm_roles(user_id, user.roles)
            case UpdateProfile(user_id=user_id, user=user):
                admin.update_profile(user_id, user)
            case SetPassword(user_id=user_id, password=password):
                admin.set_password(user_id, password)
            case AddRoles(user_id=user_id, roles=roles):
                admin.add_realm_roles(user_id, roles)
            case RemoveRoles(user_id=user_id, roles=roles):
                admin.remove_realm_roles(user_id, roles)
    return actions


def main() -> None:
    settings = SeedSettings()  # type: ignore[call-arg]  # required fields come from the environment
    admin = KeycloakAdminClient(
        settings.keycloak_url,
        settings.keycloak_realm,
        settings.keycloak_admin_user,
        settings.keycloak_admin_password,
    )
    actions = run_seed(admin, load_users(settings.users_file), settings.reset_passwords)
    if not actions:
        print("keycloak-seed: no changes")
    for action in actions:
        print(f"keycloak-seed: {action!r}")


if __name__ == "__main__":
    main()
```

Run: `uv run pytest tools/keycloak-tools/tests/test_seed.py -v`
Expected: all PASS.

- [ ] **Step 8: Add a boundary test for the HTTP client with `respx`**

`tools/keycloak-tools/tests/test_admin_client.py`:
```python
import json

import httpx
import respx

from keycloak_tools.admin_client import KeycloakAdminClient
from keycloak_tools.users import DesiredUser

BASE = "http://kc.test"
USERS = f"{BASE}/admin/realms/shortener/users"


def make_client() -> KeycloakAdminClient:
    respx.post(f"{BASE}/realms/master/protocol/openid-connect/token").respond(
        json={"access_token": "admin-token"}
    )
    return KeycloakAdminClient(BASE, "shortener", "kcadmin", "pw", httpx.Client(base_url=BASE))


@respx.mock
def test_list_users_includes_realm_roles_and_sends_bearer_token():
    client = make_client()
    users_route = respx.get(USERS).respond(
        json=[{"id": "u1", "username": "alice", "email": "a@x.test", "firstName": "A",
               "lastName": "B", "enabled": True}]
    )
    respx.get(f"{USERS}/u1/role-mappings/realm").respond(
        json=[{"name": "admin"}, {"name": "default-roles-shortener"}]
    )
    [alice] = client.list_users()
    assert alice.roles == frozenset({"admin", "default-roles-shortener"})
    assert users_route.calls.last.request.headers["Authorization"] == "Bearer admin-token"


@respx.mock
def test_create_user_returns_id_from_location_header():
    client = make_client()
    route = respx.post(USERS).respond(201, headers={"Location": f"{USERS}/new-id"})
    user = DesiredUser(username="nora", email="n@x.test", first_name="N", last_name="R",
                       password="pw")
    assert client.create_user(user) == "new-id"
    assert b'"temporary":false' in route.calls.last.request.content.replace(b" ", b"")


@respx.mock
def test_profile_update_and_password_reset_payloads():
    client = make_client()
    put_user = respx.put(f"{USERS}/u1").respond(204)
    put_password = respx.put(f"{USERS}/u1/reset-password").respond(204)
    user = DesiredUser(username="nora", email="n@x.test", first_name="N", last_name="R",
                       password="pw")
    client.update_profile("u1", user)
    client.set_password("u1", "new-pw")
    profile = json.loads(put_user.calls.last.request.content)
    assert profile["enabled"] is True
    assert profile["email"] == "n@x.test"
    assert json.loads(put_password.calls.last.request.content) == {
        "type": "password", "value": "new-pw", "temporary": False,
    }


@respx.mock
def test_role_changes_send_full_role_representations():
    client = make_client()
    respx.get(f"{BASE}/admin/realms/shortener/roles/editor").respond(
        json={"id": "r1", "name": "editor"}
    )
    add = respx.post(f"{USERS}/u1/role-mappings/realm").respond(204)
    remove = respx.delete(f"{USERS}/u1/role-mappings/realm").respond(204)
    client.add_realm_roles("u1", frozenset({"editor"}))
    client.remove_realm_roles("u1", frozenset({"editor"}))
    assert json.loads(add.calls.last.request.content) == [{"id": "r1", "name": "editor"}]
    assert json.loads(remove.calls.last.request.content) == [{"id": "r1", "name": "editor"}]
```

Run: `uv run pytest tools/keycloak-tools/tests -v`
Expected: all PASS.

- [ ] **Step 9: Write the failing e2e seed test**

`tests/e2e/test_seed.py`:
```python
import base64
import json
from pathlib import Path

import httpx
import pytest

from keycloak_tools.admin_client import KeycloakAdminClient
from keycloak_tools.seed import run_seed
from keycloak_tools.users import load_users

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_ROLES = {
    "alice": {"admin"},
    "eddie": {"editor"},
    "erin": {"editor"},
    "victor": {"viewer"},
    "nora": set(),
}


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.mark.parametrize("username", sorted(EXPECTED_ROLES))
def test_seeded_user_gets_token_with_roles_audience_and_sub(e2e_settings, username):
    response = httpx.post(
        f"{e2e_settings.keycloak_url}/realms/shortener/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "shortener-dev",
              "username": username, "password": "password", "scope": "openid"},
        timeout=10,
    )
    assert response.status_code == 200, response.text
    token_claims = claims(response.json()["access_token"])
    assert token_claims["iss"] == "http://localhost:8080/realms/shortener"
    assert token_claims["sub"]
    assert token_claims["preferred_username"] == username
    aud = token_claims["aud"]
    assert "shortener-api" in ([aud] if isinstance(aud, str) else aud)
    roles = set(token_claims.get("realm_access", {}).get("roles", []))
    assert roles & {"admin", "editor", "viewer"} == EXPECTED_ROLES[username]


def test_reseed_is_noop(e2e_settings):
    admin = KeycloakAdminClient(
        e2e_settings.keycloak_url, "shortener",
        e2e_settings.keycloak_admin_user, e2e_settings.keycloak_admin_password,
    )
    assert run_seed(admin, load_users(REPO_ROOT / "infra/keycloak/users.yaml")) == []
```
Run: `uv run pytest -m e2e tests/e2e/test_seed.py -v`
Expected: FAIL. Token requests return `401 invalid_grant` (the users don't exist yet).

- [ ] **Step 10: Add the image, compose job, and `make up` step**

`tools/keycloak-tools/Dockerfile`:
```dockerfile
# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package keycloak-tools

FROM python:3.12-slim
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
USER 10001
ENTRYPOINT ["python", "-m", "keycloak_tools.seed"]
```

Add to `docker-compose.yml` under `services:`:
```yaml
  keycloak-seed:
    build:
      context: .
      dockerfile: tools/keycloak-tools/Dockerfile
    image: shortener-keycloak-tools:${GIT_SHA:-dev}
    environment:
      KEYCLOAK_URL: http://keycloak:8080
      KEYCLOAK_REALM: shortener
      KEYCLOAK_ADMIN_USER: ${KEYCLOAK_ADMIN_USER}
      KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
      USERS_FILE: /config/users.yaml
      RESET_PASSWORDS: ${RESET_PASSWORDS:-false}
    volumes:
      - ./infra/keycloak/users.yaml:/config/users.yaml:ro
    depends_on:
      keycloak:
        condition: service_healthy
    restart: "no"
```

Append to the `up` target in the `Makefile` (after the `migrate` line):
```make
	$(COMPOSE) run --rm --build keycloak-seed
```

- [ ] **Step 11: Run seeding twice and the e2e test**

Run:
```bash
make up                 # logs one CreateUser per user
make seed-users         # logs "keycloak-seed: no changes"
uv run pytest -m e2e tests/e2e/test_seed.py -v
```
Expected: as commented; all e2e tests PASS.

- [ ] **Step 12: Quality gates and commit**

Run: `make check`
Expected: lint, mypy, and tests pass; overall coverage ≥ 80%; pure modules ≥ 90%.

```bash
git add tools/keycloak-tools infra/keycloak/users.yaml pyproject.toml uv.lock Makefile docker-compose.yml tests/e2e/test_seed.py
git commit -m "feat(tools): idempotent keycloak user seeding with demo users

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: Observability backend, `make token`, README

**Files:**
- Create: `tools/keycloak-tools/src/keycloak_tools/token.py`
- Create: `README.md`
- Modify: `docker-compose.yml` (add `otel-lgtm`), `Makefile` (`up`), `docs/superpowers/specs/2026-10-01-url-shortener-design.md` (§3.3 layout: seeding package)
- Test: `tools/keycloak-tools/tests/test_token.py`, `tests/e2e/test_observability.py`

**Interfaces:**
- Produces:
  - `keycloak_tools.token.fetch_token(base_url: str, realm: str, username: str, password: str, client_id: str = "shortener-dev", http: httpx.Client | None = None) -> str`
  - `keycloak_tools.token.TokenError(RuntimeError)`
  - `make token USER=<name>`, which prints a raw access token
  - OTLP endpoints `http://otel-lgtm:4317` (gRPC) and `http://otel-lgtm:4318` (HTTP) on the compose network; Grafana at `http://localhost:3000`

- [ ] **Step 1: Write the failing token tests**

`tools/keycloak-tools/tests/test_token.py`:
```python
import httpx
import pytest
import respx

from keycloak_tools.token import TokenError, fetch_token

URL = "http://kc.test/realms/shortener/protocol/openid-connect/token"


@respx.mock
def test_fetch_token_uses_password_grant_on_dev_client():
    route = respx.post(URL).respond(json={"access_token": "tok"})
    token = fetch_token("http://kc.test", "shortener", "alice", "password", http=httpx.Client())
    assert token == "tok"
    form = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert form == {
        "grant_type": "password",
        "client_id": "shortener-dev",
        "username": "alice",
        "password": "password",
        "scope": "openid",
    }


@respx.mock
def test_rejected_credentials_raise_token_error_with_keycloak_message():
    respx.post(URL).respond(401, json={"error": "invalid_grant", "error_description": "Invalid user credentials"})
    with pytest.raises(TokenError, match="Invalid user credentials"):
        fetch_token("http://kc.test", "shortener", "alice", "wrong", http=httpx.Client())
```

Run: `uv run pytest tools/keycloak-tools/tests/test_token.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'keycloak_tools.token'`.

- [ ] **Step 2: Implement `token.py`**

`tools/keycloak-tools/src/keycloak_tools/token.py`:
```python
"""DEV ONLY: fetch an access token for a seeded user via the `shortener-dev` client.

    uv run python -m keycloak_tools.token alice          # or: make token USER=alice
"""

import argparse
import os

import httpx


class TokenError(RuntimeError):
    pass


def fetch_token(
    base_url: str,
    realm: str,
    username: str,
    password: str,
    client_id: str = "shortener-dev",
    http: httpx.Client | None = None,
) -> str:
    client = http or httpx.Client(timeout=10.0)
    response = client.post(
        f"{base_url}/realms/{realm}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
            "scope": "openid",
        },
    )
    if response.status_code != 200:
        detail = response.json().get("error_description", response.text)
        raise TokenError(f"token request for {username!r} failed ({response.status_code}): {detail}")
    return str(response.json()["access_token"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--password", default="password")
    parser.add_argument("--keycloak-url", default=os.environ.get("KEYCLOAK_URL", "http://localhost:8080"))
    parser.add_argument("--realm", default="shortener")
    args = parser.parse_args()
    print(fetch_token(args.keycloak_url, args.realm, args.username, args.password))


if __name__ == "__main__":
    main()
```

Run: `uv run pytest tools/keycloak-tools/tests/test_token.py -v`
Expected: all PASS.

- [ ] **Step 3: Write the failing observability e2e test**

`tests/e2e/test_observability.py`:
```python
import httpx
import pytest

pytestmark = pytest.mark.e2e


def test_grafana_is_healthy(e2e_settings):
    response = httpx.get(f"{e2e_settings.grafana_url}/api/health", timeout=10)
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


@pytest.mark.parametrize("signal", ["traces", "metrics", "logs"])
def test_otlp_http_accepts_empty_exports(e2e_settings, signal):
    key = {"traces": "resourceSpans", "metrics": "resourceMetrics", "logs": "resourceLogs"}[signal]
    response = httpx.post(f"{e2e_settings.otlp_http_url}/v1/{signal}", json={key: []}, timeout=10)
    assert response.status_code == 200
```

Run: `uv run pytest -m e2e tests/e2e/test_observability.py -v`
Expected: FAIL with `ConnectError`.

- [ ] **Step 4: Add `otel-lgtm` and extend `make up`**

Add to `docker-compose.yml` under `services:`:
```yaml
  otel-lgtm:
    image: grafana/otel-lgtm:0.11.10
    ports:
      - "3000:3000"   # Grafana (anonymous admin in this image; local only)
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP
    volumes:
      - lgtm-data:/data
    # No healthcheck: readiness is asserted by tests/e2e/test_observability.py.
```
Add `lgtm-data:` under the top-level `volumes:`.

In the `Makefile`, change the first line of `up` to:
```make
	$(COMPOSE) up -d --build --wait postgres elasticmq keycloak otel-lgtm
```

Run: `make up && uv run pytest -m e2e tests/e2e -v`
Expected: every e2e test from Tasks 5–9 PASSES. Then `make token USER=alice` prints a JWT.

- [ ] **Step 5: Write the README**

`README.md`:
````markdown
# URL Shortener Platform

A URL shortener with an API, an HTMX admin UI, an event-driven click pipeline (SQS), Keycloak auth,
and OpenTelemetry. Runs entirely locally; designed to map onto AWS (see the spec).

- Design spec: `docs/superpowers/specs/2026-10-01-url-shortener-design.md`
- Implementation plans: `docs/superpowers/plans/`

## Prerequisites

- Docker with Compose v2.20+ (Docker Desktop or colima: `colima start --cpu 4 --memory 8`)
- [uv](https://docs.astral.sh/uv/) 0.12+

colima users: integration tests use testcontainers, which needs:

```bash
export DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock"
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock
```

## Quickstart

```bash
make sync     # install the workspace
make up       # start backing services, run migrations, seed Keycloak users
make e2e      # verify the running stack
make token USER=eddie   # print an access token for a seeded user
make down     # stop everything and delete volumes
```

## Local services

| Service | URL | Notes |
|---|---|---|
| Postgres | `localhost:5432` | DBs `shortener`, `keycloak`; credentials in `.env` |
| Keycloak | http://localhost:8080 | Admin console: `KEYCLOAK_ADMIN_USER` / `KEYCLOAK_ADMIN_PASSWORD` from `.env` |
| ElasticMQ (SQS) | http://localhost:9324 | Stats UI: http://localhost:9325 |
| Grafana (otel-lgtm) | http://localhost:3000 | OTLP: `localhost:4317` (gRPC), `localhost:4318` (HTTP) |

## Seeded users (DEV ONLY, password `password`)

| User | Role |
|---|---|
| alice | admin |
| eddie, erin | editor |
| victor | viewer |
| nora | (none) |

Edit `infra/keycloak/users.yaml` and run `make seed-users` to add users or change roles. Seeding is
idempotent and never removes roles it doesn't manage. Passwords are set on creation only
(`RESET_PASSWORDS=true make seed-users` to force).

## Development

```bash
make check    # ruff, mypy --strict, tests with coverage gates
make fmt      # auto-format and fix lint
```

Migrations live in `api/alembic` and run only via `make migrate` (the `migrate` release job).

## Troubleshooting

- **Realm changes not applied:** Keycloak imports the realm only when it doesn't exist. Run `make down && make up`.
- **`Account is not fully set up` on login:** the user is missing email/first/last name in `users.yaml`.
- **Testcontainers can't find Docker (colima):** export the two variables above.
````

- [ ] **Step 6: Align the spec's layout with the seeding package**

In `docs/superpowers/specs/2026-10-01-url-shortener-design.md` §3.3, replace the line
```
  keycloak/realm-export.json, users.yaml, seed_users.py
```
with
```
  keycloak/realm-export.json, users.yaml
tools/keycloak-tools/          dev tooling package: idempotent user seeding (keycloak_tools.seed), make token
```
and in §7.5 replace `` `infra/keycloak/users.yaml` + `seed_users.py` `` with `` `infra/keycloak/users.yaml` + `keycloak_tools.seed` ``.

- [ ] **Step 7: Quality gates and commit**

Run: `make check`
Expected: all gates pass.

```bash
git add tools/keycloak-tools docker-compose.yml Makefile README.md tests/e2e/test_observability.py docs/superpowers/specs/2026-10-01-url-shortener-design.md
git commit -m "feat(infra): otel-lgtm backend, make token helper, README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Make targets `sync`, `lint`, `typecheck`, `test` (Task 1, as extended).
- Produces: a CI job `check` (the quality gates plus unit and integration tests) and a job `images` (builds every Dockerfile). Plans 2–4 add their Dockerfiles to the `images` matrix.

- [ ] **Step 1: Write the workflow**

`.github/workflows/ci.yml`:
```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.12.1"
          enable-cache: true
      - run: uv python install 3.12
      - run: make sync
      - run: make lint
      - run: make typecheck
      - run: make test   # integration tests use the runner's Docker daemon via testcontainers
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: coverage
          path: coverage.xml

  images:
    runs-on: ubuntu-24.04
    strategy:
      matrix:
        dockerfile:
          - api/Dockerfile
          - tools/keycloak-tools/Dockerfile
    steps:
      - uses: actions/checkout@v4
      - run: docker build -f ${{ matrix.dockerfile }} --build-arg GIT_SHA=${{ github.sha }} .
```

- [ ] **Step 2: Lint the workflow**

Run: `uvx --from actionlint-py actionlint .github/workflows/ci.yml`
Expected: no output (no problems).

- [ ] **Step 3: Run the same steps locally**

Run: `make sync && make check && docker build -f api/Dockerfile . && docker build -f tools/keycloak-tools/Dockerfile .`
Expected: everything passes, and both images build.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: quality gates, tests, and image builds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Plan 1 Done When

- `make down && make up` on a clean machine brings up Postgres, ElasticMQ, Keycloak, and otel-lgtm; runs migrations; and seeds the five users.
- Running `make up` a second time makes no changes.
- `make check` passes (lint, `mypy --strict`, tests, coverage gates).
- `make e2e` passes.
- `make token USER=eddie` prints a token whose `aud` contains `shortener-api` and whose `realm_access.roles` contains `editor`.
