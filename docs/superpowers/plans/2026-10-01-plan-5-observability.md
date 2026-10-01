# Plan 5 — Observability, Dashboard, and CI E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish spec §10 across all three services, then run the e2e suite in CI:
- **Distributed tracing.** One UI action becomes one trace from the admin UI through the API to Postgres. Clicks travel through SQS as a `traceparent` attribute, and the processor's batch span links back to each producer.
- **Structured JSON logs.** Every line carries `trace_id`/`span_id` and is also exported to Loki.
- **`trace_id` on error pages and in 5xx problem responses.**
- **A provisioned "Shortener Overview" Grafana dashboard.**
- **E2E in CI.** The whole stack's e2e suite runs on every push.

**Architecture:** A new workspace package, `libs/shortener-observability` (`shortener_observability`), owns everything the three services had duplicated or lacked:
- `configure_telemetry()` sets up the resource, trace, metric and log providers with OTLP/HTTP exporters when an endpoint is configured.
- A JSON log formatter adds the current span's ids.
- `current_traceparent()`, `span_link_from_traceparent()` and `current_trace_id()` handle propagation.

Each service's `build_deps` calls `configure_telemetry` and passes the providers into its app or runtime, which instruments them:
- **API:** FastAPI, SQLAlchemy, httpx, botocore
- **Admin:** FastAPI, SQLAlchemy, httpx (so API calls carry `traceparent`)
- **Processor:** SQLAlchemy, botocore, plus a manual batch span with links

Tests use in-memory exporters; nothing global is installed in unit tests.

**Tech Stack:** OpenTelemetry Python SDK 1.45.0, contrib instrumentations 0.66b0 (FastAPI, SQLAlchemy, httpx, botocore), OTLP/HTTP exporters for traces, metrics and logs, Grafana (in `grafana/otel-lgtm`, with Tempo, Loki and Prometheus), and GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-01-url-shortener-design.md`. Read §5.2–5.3 (`traceparent`, span links), §8 (error page `trace_id`), §9, §10, §13, and §15 before starting.

**Plan series:** 1 Foundation, 2 API, 3 Click processor, 4 Admin UI (done) → **5 Observability + CI E2E (this, final)**.

**Verified against the running stack before writing:**
- **Packages:** OTel SDK 1.45.0 and contrib instrumentations 0.66b0, with the import paths used below. Logs are in `opentelemetry.sdk._logs`; the exporter is `opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter`.
- **Prometheus names:** OTLP metrics land in Prometheus with `_total` and `_seconds` suffixes and `job`/`service_name` = `service.name` (e.g. `shortener_redirects_total{job="shortener-api",result="ok"}`). The HTTP server metric is `http_server_request_duration_seconds_*` with `http_response_status_code`.
- **Grafana:** the `otel-lgtm` image provisions dashboards from YAML files in `/otel-lgtm/grafana/conf/provisioning/dashboards/`, and its datasource UIDs are `prometheus`, `tempo` and `loki`.

**Suggested execution batches** (batched subagent-driven execution): (Tasks 1–2), (3–4), (5–6).

## Global Constraints

- Everything in Plans 1–4's Global Constraints still applies:
  - config only from the environment, failing fast
  - pinned versions
  - no SQLite; no `sleep` in unit/integration tests (e2e may poll)
  - fakes over mocks; in-memory OTel exporters in tests
  - `mypy --strict` on `src/`
  - coverage ≥ 80% overall and ≥ 90% on pure modules
  - Run `uv run ruff format . && make check` before every commit, chained with `&&`, never piped. Never commit on a red gate.
- Commit trailer: the implementing model's own name, e.g. `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Pin OTel versions:** `opentelemetry-api==1.45.0`, `opentelemetry-sdk==1.45.0`, `opentelemetry-exporter-otlp-proto-http==1.45.0`, and `opentelemetry-instrumentation-{fastapi,sqlalchemy,httpx,botocore}==0.66b0`. Replace the services' existing `>=1.27` OTel ranges with these pins.
- **Resource attributes**, exactly: `service.name` (`shortener-api` | `shortener-click-processor` | `shortener-admin`), `service.version`, and `deployment.environment`.
- **Unit and integration tests never install global providers.** `configure_telemetry(..., install_globals=False)` is for tests; services call it with globals installed only in `build_deps`/`build_runtime`. Tests that need spans build a `TracerProvider` with an `InMemorySpanExporter` and pass it in.
- **No per-link attributes on metrics** (unchanged).
- **Spans must not carry secrets:**
  - no token, cookie or `Authorization` values in attributes
  - don't enable header capture
  - DB statement text is fine; parameters are not captured by the SQLAlchemy instrumentation by default, so don't turn that on
- **Logs:**
  - one JSON object per line on stdout, with keys `ts`, `level`, `logger`, `message`, `service`, and `trace_id`/`span_id` when a span is active, plus `exception`
  - also exported over OTLP to Loki when an endpoint is set
  - uvicorn's loggers propagate to the root JSON handler
- **Trace IDs** are shown or returned as 32 lowercase hex characters.
- **New workspace package `libs/shortener-observability`:** add it to the root `members`, and copy its `pyproject.toml` in **every** Dockerfile's dependency layer (api, processor, admin, keycloak-tools).
- **Running the stack:** `make check`/`make e2e` auto-detect Docker (`scripts/docker-env.sh`). Use `make up`; never `make down` locally. Keycloak is on host port 8180 locally.

## Review Focus

1. **Context crossing SQS.** A redirect made under a known incoming `traceparent` must produce a message whose `traceparent` attribute is a **child of that trace**. The processor's batch span must **link** to it (link, not parent). If there's no active span, no attribute is sent. A malformed `traceparent` on a message is ignored, never raised. Tests: Task 2 `test_publisher_carries_the_request_traceparent`; Task 3 `test_batch_span_links_to_each_producer` and `test_malformed_traceparent_is_ignored`; Task 6 e2e `test_click_trace_links_into_the_processor`.
2. **Secret leakage in telemetry.** No `Authorization`, cookie or token values in span attributes or logs, including exception logs from the admin login flow. Tests: Task 4 `test_spans_and_logs_carry_no_secrets`.
3. **Telemetry must never break a request.** With the collector down or no endpoint configured, every service still serves normally. Exporters fail in the background. Tests: Task 1 `test_configure_without_endpoint_exports_nothing_and_works`; Task 6 relies on the e2e suite still passing.
4. **Error pages and 5xx problems carry the right `trace_id`.** It must be the trace of the failing request (equal to an incoming `traceparent`'s trace id) and never a stale or other request's id. 4xx problems don't include it. Tests: Task 2 `test_5xx_problem_includes_trace_id`; Task 4 `test_error_page_shows_the_request_trace_id`.
5. **Log correlation:** a log line written inside a request span carries that span's ids, both on stdout and in the Loki copy. Tests: Task 1 `test_json_formatter_inside_span`; Task 6 e2e `test_admin_log_line_is_correlated_in_loki`.

---

## File Structure

```
libs/shortener-observability/            NEW workspace member
  pyproject.toml
  src/shortener_observability/__init__.py, py.typed
  src/shortener_observability/setup.py       Telemetry, configure_telemetry()
  src/shortener_observability/logs.py        JsonFormatter, configure_logging()
  src/shortener_observability/propagation.py current_traceparent, span_link_from_traceparent, current_trace_id
  tests/test_logs.py, tests/test_propagation.py, tests/test_setup.py
api/        pyproject (deps), deps.py (+ providers), main.py (instrumentation, build_deps), telemetry.py
            (configure_meter_provider removed), publisher.py (traceparent), errors.py (trace_id on 5xx)
processor/  pyproject, settings.py, telemetry.py (configure_meter_provider removed), batch.py (span + links),
            main.py (build_runtime/main wiring)
admin/      pyproject, settings.py (+otel fields), deps.py (+providers), api_client.py (http_client),
            main.py (instrumentation, trace_id in error pages), templates/error.html, templates/login_failed.html
scripts/gen-dashboard                              dashboard-as-code generator (uv shebang; --check for drift)
infra/otel/dashboards/shortener-overview.json      generated + committed; provisioned
infra/otel/grafana/shortener-dashboards.yaml       Grafana provisioning provider
docker-compose.yml   otel-lgtm mounts; admin OTEL env; metric export interval
tests/test_dashboard.py              dashboard structure + PromQL metric-name allowlist (unit)
tests/e2e/test_observability.py      + dashboard present, metrics, cross-SQS trace link, Loki correlation
.github/workflows/ci.yml             + e2e job (make up && make e2e; compose logs on failure)
README.md, spec                      docs
```

---

### Task 1: Shared observability library

**Files:**
- Create: `libs/shortener-observability/pyproject.toml`, `libs/shortener-observability/src/shortener_observability/{__init__,setup,logs,propagation}.py`, `py.typed`
- Test: `libs/shortener-observability/tests/{conftest,test_logs,test_propagation,test_setup}.py`
- Modify: root `pyproject.toml` (members; ruff `src`; coverage `source`), `Makefile` (`MYPY_TARGETS`), and `api/Dockerfile`, `processor/Dockerfile`, `admin/Dockerfile`, `tools/keycloak-tools/Dockerfile` (dependency-layer COPY line)

**Interfaces:**
- Produces:
  - `Telemetry`: a dataclass with `tracer_provider: TracerProvider`, `meter_provider: MeterProvider`, `logger_provider: LoggerProvider | None`, and methods `tracer(name) -> Tracer`, `meter(name) -> Meter`, `shutdown() -> None` (idempotent)
  - `configure_telemetry(*, service_name, service_version, environment, otlp_endpoint: str | None, metric_export_interval_ms: int = 10_000, log_level: str = "INFO", install_globals: bool = True) -> Telemetry`
  - `JsonFormatter(service_name)`
  - `configure_logging(service_name, *, level="INFO", extra_handlers=()) -> None`
  - `current_traceparent() -> str | None`
  - `span_link_from_traceparent(value: str | None) -> Link | None`
  - `current_trace_id() -> str | None` (32 lowercase hex characters)

- [ ] **Step 1: Create the package and register it**

`libs/shortener-observability/pyproject.toml`:
```toml
[project]
name = "shortener-observability"
version = "0.1.0"
description = "Shared OpenTelemetry setup, JSON logging, and trace propagation helpers."
requires-python = ">=3.12"
dependencies = [
  "opentelemetry-api==1.45.0",
  "opentelemetry-sdk==1.45.0",
  "opentelemetry-exporter-otlp-proto-http==1.45.0",
]

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shortener_observability"]
```
Create `__init__.py` with `"""Shared observability: telemetry setup, JSON logs, trace propagation."""` (the exports come in Step 5) and an empty `py.typed`.

Root `pyproject.toml`:
- add `"libs/shortener-observability"` to `members`
- add `"libs/shortener-observability/src"` to ruff `src`
- add `"shortener_observability"` to coverage `source`

`Makefile`: append ` libs/shortener-observability/src` to `MYPY_TARGETS`.

In each of the **four** Dockerfiles (`api`, `processor`, `admin`, `tools/keycloak-tools`), add `COPY libs/shortener-observability/pyproject.toml libs/shortener-observability/` to the dependency layer.

Run: `uv lock && uv sync --all-packages && for f in api processor admin tools/keycloak-tools; do docker build -q -f $f/Dockerfile . >/dev/null || exit 1; done && echo ok`
Expected: `ok`.

- [ ] **Step 2: Write the failing tests**

`libs/shortener-observability/tests/conftest.py`:
```python
import logging
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture
def spans() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(spans):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    return provider.get_tracer("test")


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """configure_logging rewires the root logger; put it back after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
```

`libs/shortener-observability/tests/test_logs.py`:
```python
import json
import logging
import sys

from shortener_observability.logs import JsonFormatter, configure_logging


def record(message: str = "hello", exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("svc.module", logging.WARNING, __file__, 1, message, None, exc_info)


def test_json_formatter_outside_span():
    line = json.loads(JsonFormatter("shortener-api").format(record()))
    assert line["level"] == "WARNING" and line["logger"] == "svc.module" and line["message"] == "hello"
    assert line["service"] == "shortener-api"
    assert line["ts"].endswith("Z")
    assert "trace_id" not in line and "span_id" not in line


def test_json_formatter_inside_span(tracer):
    with tracer.start_as_current_span("work") as span:
        line = json.loads(JsonFormatter("shortener-api").format(record()))
    ctx = span.get_span_context()
    assert line["trace_id"] == f"{ctx.trace_id:032x}"
    assert line["span_id"] == f"{ctx.span_id:016x}"


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        line = json.loads(JsonFormatter("svc").format(record(exc_info=sys.exc_info())))
    assert "ValueError: boom" in line["exception"]


def test_configure_logging_writes_json_to_stdout_and_captures_uvicorn(capsys):
    configure_logging("shortener-api", level="INFO")
    logging.getLogger("uvicorn.access").info("GET / 200")
    logging.getLogger("app").debug("hidden at INFO")
    out = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["message"] for line in out] == ["GET / 200"]
    assert logging.getLogger("uvicorn.access").propagate is True
```

`libs/shortener-observability/tests/test_propagation.py`:
```python
from shortener_observability.propagation import (
    current_trace_id,
    current_traceparent,
    span_link_from_traceparent,
)


def test_no_traceparent_or_trace_id_outside_a_span():
    assert current_traceparent() is None
    assert current_trace_id() is None


def test_traceparent_inside_a_span(tracer):
    with tracer.start_as_current_span("work") as span:
        value = current_traceparent()
        trace_id = current_trace_id()
    ctx = span.get_span_context()
    assert value == f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-01"
    assert trace_id == f"{ctx.trace_id:032x}"


def test_span_link_round_trip(tracer):
    with tracer.start_as_current_span("producer") as span:
        value = current_traceparent()
    link = span_link_from_traceparent(value)
    assert link is not None
    assert link.context.trace_id == span.get_span_context().trace_id
    assert link.context.span_id == span.get_span_context().span_id


def test_invalid_traceparents_give_no_link():
    for value in (None, "", "garbage", "00-" + "0" * 32 + "-" + "0" * 16 + "-01", "01-zz-yy-00"):
        assert span_link_from_traceparent(value) is None
```

`libs/shortener-observability/tests/test_setup.py`:
```python
import logging

from shortener_observability.setup import configure_telemetry


def test_configure_without_endpoint_exports_nothing_and_works(capsys):
    telemetry = configure_telemetry(
        service_name="shortener-api", service_version="abc123", environment="local",
        otlp_endpoint=None, install_globals=False,
    )  # fmt: skip
    attrs = telemetry.tracer_provider.resource.attributes
    assert (attrs["service.name"], attrs["service.version"], attrs["deployment.environment"]) == (
        "shortener-api", "abc123", "local",
    )  # fmt: skip
    assert telemetry.logger_provider is None
    with telemetry.tracer("t").start_as_current_span("s"):
        logging.getLogger("x").warning("inside")
    telemetry.meter("m").create_counter("c").add(1)
    assert '"message": "inside"' in capsys.readouterr().out
    telemetry.shutdown()
    telemetry.shutdown()  # idempotent


def test_configure_with_endpoint_wires_otlp_logs_without_network():
    telemetry = configure_telemetry(
        service_name="shortener-api", service_version="v", environment="local",
        otlp_endpoint="http://127.0.0.1:1", install_globals=False,
    )  # fmt: skip
    assert telemetry.logger_provider is not None
    assert any(type(h).__name__ == "LoggingHandler" for h in logging.getLogger().handlers)
    telemetry.shutdown()  # must not raise even though the collector is unreachable
```

Run: `uv run pytest libs/shortener-observability -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`libs/shortener-observability/src/shortener_observability/logs.py`:
```python
"""JSON-lines logging on stdout with the active span's ids (spec §10)."""

import json
import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime

from opentelemetry import trace

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.fromtimestamp(record.created, UTC)
        payload: dict[str, object] = {
            "ts": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            payload["trace_id"] = f"{ctx.trace_id:032x}"
            payload["span_id"] = f"{ctx.span_id:016x}"
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    service_name: str, *, level: str = "INFO", extra_handlers: Iterable[logging.Handler] = ()
) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(JsonFormatter(service_name))
    root.addHandler(stream)
    for handler in extra_handlers:
        root.addHandler(handler)
    root.setLevel(level)
    for name in _UVICORN_LOGGERS:  # uvicorn installs its own text handlers; route them through ours
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
```

`libs/shortener-observability/src/shortener_observability/propagation.py`:
```python
"""W3C trace-context helpers for crossing SQS and for user-visible references."""

from opentelemetry import trace
from opentelemetry.trace import Link
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROPAGATOR = TraceContextTextMapPropagator()


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get("traceparent")


def span_link_from_traceparent(value: str | None) -> Link | None:
    if not value:
        return None
    context = _PROPAGATOR.extract({"traceparent": value})
    span_context = trace.get_current_span(context).get_span_context()
    return Link(span_context) if span_context.is_valid else None


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else None
```

`libs/shortener-observability/src/shortener_observability/setup.py`:
```python
"""One call per service: resource, traces, metrics, logs (OTLP/HTTP when an endpoint is set)."""

import logging
from dataclasses import dataclass, field

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

from shortener_observability.logs import configure_logging


@dataclass
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider | None
    _shut_down: bool = field(default=False, repr=False)

    def tracer(self, name: str) -> Tracer:
        return self.tracer_provider.get_tracer(name)

    def meter(self, name: str) -> Meter:
        return self.meter_provider.get_meter(name)

    def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        if self.logger_provider is not None:
            self.logger_provider.shutdown()


def configure_telemetry(
    *,
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: str | None,
    metric_export_interval_ms: int = 10_000,
    log_level: str = "INFO",
    install_globals: bool = True,
) -> Telemetry:
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )
    base = otlp_endpoint.rstrip("/") if otlp_endpoint else None

    tracer_provider = TracerProvider(resource=resource)
    readers: list[MetricReader] = []
    logger_provider: LoggerProvider | None = None
    extra_handlers: list[logging.Handler] = []
    if base:
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
        readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
                export_interval_millis=metric_export_interval_ms,
            )
        )
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs"))
        )
        extra_handlers.append(LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider))
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)

    configure_logging(service_name, level=log_level, extra_handlers=extra_handlers)
    if install_globals:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
    return Telemetry(tracer_provider, meter_provider, logger_provider)
```

`libs/shortener-observability/src/shortener_observability/__init__.py`:
```python
"""Shared observability: telemetry setup, JSON logs, trace propagation."""

from shortener_observability.logs import JsonFormatter, configure_logging
from shortener_observability.propagation import (
    current_trace_id,
    current_traceparent,
    span_link_from_traceparent,
)
from shortener_observability.setup import Telemetry, configure_telemetry

__all__ = [
    "JsonFormatter",
    "Telemetry",
    "configure_logging",
    "configure_telemetry",
    "current_trace_id",
    "current_traceparent",
    "span_link_from_traceparent",
]
```

Run: `uv run pytest libs/shortener-observability -q`
Expected: all PASS. If mypy complains about the private `opentelemetry.sdk._logs` module (`attr-defined` or similar), keep the import. It's the SDK's only log API in 1.45; add a targeted `# type: ignore[...]` with the reason.

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff format . && make check && git add libs/shortener-observability pyproject.toml uv.lock Makefile api/Dockerfile processor/Dockerfile admin/Dockerfile tools/keycloak-tools/Dockerfile && git commit -m "feat(observability): shared telemetry setup, JSON logs, and trace propagation helpers

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 2: API — tracing, trace context into SQS, `trace_id` on 5xx

**Files:**
- Modify: `api/pyproject.toml`, `api/src/shortener_api/{settings,deps,main,telemetry,publisher,errors}.py`
- Modify: `api/tests/conftest.py` (autouse logging restore), `api/tests/unit/test_main.py`, `api/tests/unit/test_publisher.py`
- Create: `api/tests/unit/test_tracing.py`, `api/tests/integration/test_redirect_tracing.py`

**Interfaces:**
- Consumes: `configure_telemetry`, `current_traceparent`, `current_trace_id` (Task 1).
- Produces:
  - `ApiSettings` gains `otel_metric_export_interval_ms: int = 10_000` and `log_level: str = "INFO"`
  - `AppDeps` gains three optional, kw-only fields: `tracer_provider: TracerProvider | None = None`, `meter_provider: MeterProvider | None = None`, `telemetry_shutdown: Callable[[], None] | None = None`
  - `create_app(deps)`:
    - when `deps.tracer_provider` is set, instruments FastAPI (`excluded_urls="healthz,readyz"`) and the engine's SQLAlchemy
    - the lifespan calls `telemetry_shutdown` last
  - `build_deps(settings, *, install_globals: bool = True)` calls `configure_telemetry(service_name="shortener-api", ...)`. It also instruments the JWKS httpx client, and botocore (only when `install_globals`, because it patches globally).
  - `telemetry.configure_meter_provider` is **removed**; `ApiTelemetry(meter)` stays.
  - `BufferedClickPublisher.publish(event)` captures `current_traceparent()` at publish time and sends it as the SQS `traceparent` attribute.
  - `InMemoryClickPublisher` gains `.traceparents: list[str | None]`.
  - `errors.problem_response`: for `status >= 500`, adds `"trace_id": current_trace_id()` when a span is active.

- [ ] **Step 1: Dependencies and logging-safe tests**

In `api/pyproject.toml`:
- Replace the three `opentelemetry-*` lines with:
```toml
  "shortener-observability",
  "opentelemetry-api==1.45.0",
  "opentelemetry-sdk==1.45.0",
  "opentelemetry-instrumentation-fastapi==0.66b0",
  "opentelemetry-instrumentation-sqlalchemy==0.66b0",
  "opentelemetry-instrumentation-httpx==0.66b0",
  "opentelemetry-instrumentation-botocore==0.66b0",
```
- Add `shortener-observability = { workspace = true }` under `[tool.uv.sources]`.

Then run `uv lock && uv sync --all-packages`.

Add to `api/tests/conftest.py` (with `import logging` and `from collections.abc import Iterator` at the top):
```python
@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """build_deps() rewires the root logger (JSON handler); undo it after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
```

- [ ] **Step 2: Write the failing tests**

`api/tests/unit/test_tracing.py`:
```python
import dataclasses

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
INCOMING = {"traceparent": f"00-{TRACE}-00f067aa0ba902b7-01"}


@pytest.fixture
def spans() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def deps(deps, spans):  # overrides the unit deps fixture with tracing enabled
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    return dataclasses.replace(deps, tracer_provider=provider, meter_provider=MeterProvider())


async def test_requests_create_server_spans_continuing_the_incoming_trace(client, token_for, spans):
    response = await client.get("/api/v1/me", headers=token_for("eddie") | INCOMING)
    assert response.status_code == 200
    server = [s for s in spans.get_finished_spans() if s.kind.name == "SERVER"]
    assert server and all(f"{s.context.trace_id:032x}" == TRACE for s in server)


async def test_health_endpoints_are_not_traced(client, spans):
    await client.get("/healthz")
    assert spans.get_finished_spans() == ()


async def test_5xx_problem_includes_trace_id(client, spans):
    response = await client.get("/aZ3kQ9x", headers=INCOMING)  # unit deps: database unreachable → 503
    assert response.status_code == 503
    assert response.json()["trace_id"] == TRACE


async def test_4xx_problem_has_no_trace_id(client):
    response = await client.get("/api/v1/me", headers=INCOMING)
    assert response.status_code == 401
    assert "trace_id" not in response.json()
```

Append to `api/tests/unit/test_publisher.py` (with `from opentelemetry.sdk.trace import TracerProvider` at the top):
```python
async def test_publisher_carries_the_request_traceparent(make_publisher):
    sender = FakeSender()
    publisher = make_publisher(sender)
    tracer = TracerProvider().get_tracer("t")
    with tracer.start_as_current_span("redirect") as span:
        publisher.publish(event(1))
    publisher.publish(event(2))  # no active span
    await publisher.flush_once()
    with_ctx, without_ctx = sender.calls[0]
    ctx = span.get_span_context()
    assert with_ctx.attributes["traceparent"] == f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-01"
    assert "traceparent" not in without_ctx.attributes


async def test_in_memory_publisher_records_traceparents():
    publisher = InMemoryClickPublisher()
    with TracerProvider().get_tracer("t").start_as_current_span("s"):
        publisher.publish(event(1))
    assert publisher.traceparents[0] is not None and publisher.traceparents[0].startswith("00-")
```

`api/tests/integration/test_redirect_tracing.py`:
```python
import dataclasses

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

pytestmark = pytest.mark.integration
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


@pytest.fixture
def spans():
    return InMemorySpanExporter()


@pytest.fixture
def deps(deps, spans):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    return dataclasses.replace(deps, tracer_provider=provider, meter_provider=MeterProvider())


async def test_redirect_trace_covers_postgres_and_reaches_the_event(client, deps, insert_link, spans):
    insert_link(code="trace01")
    response = await client.get("/trace01", headers={"traceparent": f"00-{TRACE}-00f067aa0ba902b7-01"})
    assert response.status_code == 302
    finished = spans.get_finished_spans()
    assert any(s.attributes.get("db.system") == "postgresql" for s in finished)
    assert all(f"{s.context.trace_id:032x}" == TRACE for s in finished)
    [traceparent] = deps.publisher.traceparents
    assert traceparent is not None and traceparent.startswith(f"00-{TRACE}-")
```
The `db.system` attribute name depends on the instrumentation's semantic-convention mode. If the finished DB span uses `db.system.name` instead, assert on whichever key the installed 0.66b0 instrumentation actually emits. Print `[dict(s.attributes) for s in finished]` once to check.

In `api/tests/unit/test_main.py`:
- Delete `test_meter_provider_carries_resource_attributes` and its `configure_meter_provider` import.
- Change `test_build_deps_uses_real_components_without_network` to call `build_deps(settings, install_globals=False)`, and add these assertions:
```python
    assert deps.tracer_provider is not None
    assert deps.tracer_provider.resource.attributes["service.name"] == "shortener-api"
    assert deps.telemetry_shutdown is not None
    deps.telemetry_shutdown()
```

Run: `uv run pytest api/tests/unit -q`
Expected: FAIL. `dataclasses.replace` rejects the unknown `tracer_provider` field, the publisher tests fail on the missing `traceparents`, and `test_main` fails on the `install_globals` keyword.

- [ ] **Step 3: Implement**

`settings.py` (`ApiSettings`): add
```python
    otel_metric_export_interval_ms: int = Field(default=10_000, ge=1_000)
    log_level: str = "INFO"
```

`deps.py`:
- Under `TYPE_CHECKING`, add `from opentelemetry.sdk.metrics import MeterProvider` and `from opentelemetry.sdk.trace import TracerProvider`. Add `from collections.abc import Callable` at the top.
- Append these fields to `AppDeps`:
```python
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    telemetry_shutdown: Callable[[], None] | None = None
```

`publisher.py`:
- Add `from shortener_observability import current_traceparent`.
- `InMemoryClickPublisher.__init__` adds `self.traceparents: list[str | None] = []`, and `publish` appends `current_traceparent()`.
- `BufferedClickPublisher`:
  - `self._queue: asyncio.Queue[tuple[ClickEvent, str | None]]`
  - `publish` does `put_nowait((event, current_traceparent()))`
  - `_take_batch` returns `list[tuple[ClickEvent, str | None]]`
  - `_send_with_retries(batch)` encodes with `encode(event, traceparent=parent)` for each `(event, parent)` in `pending`
  - The rest of the retry, `_in_flight` and drop logic is unchanged; it counts list lengths, so tuples work as-is.

`errors.py`:
- Add `from shortener_observability import current_trace_id`.
- In `problem_response`, after `body.update(extra or {})`:
```python
    if status >= 500 and (trace_id := current_trace_id()) is not None:
        body["trace_id"] = trace_id
```

`telemetry.py`: delete `configure_meter_provider` and the imports only it used (`OTLPMetricExporter`, `MeterProvider`, `MetricReader`, `PeriodicExportingMetricReader`, `Resource`, `ApiSettings`).

`main.py`:
- Add the imports:
```python
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from shortener_observability import configure_telemetry
```
- Remove the `configure_meter_provider` import.
- In `_lifespan`'s `finally`, after `engine.dispose()`:
```python
        if deps.telemetry_shutdown is not None:
            deps.telemetry_shutdown()
```
- In `create_app`, after the routers are included:
```python
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
```
- Replace `build_deps` with:
```python
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
        BotocoreInstrumentor().instrument(tracer_provider=telemetry.tracer_provider)
    jwks = HttpJwksProvider(
        f"{settings.oidc_internal_url}/protocol/openid-connect/certs", jwks_http, clock
    )
    sqs = boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.sqs_endpoint_url,
        config=Config(connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1, "mode": "standard"}),
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
```
Keep the existing `Config` import and the `boto3` client arguments **exactly** as they are on `main` today, including the `total_max_attempts=1` fix. Only the telemetry lines are new.

Run: `uv run pytest api -q`
Expected: all PASS, unit and integration.

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff format . && make check && git add api uv.lock && git commit -m "feat(api): distributed tracing, traceparent into SQS, trace_id on 5xx problems

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 3: Processor — batch span with producer links, DB/SQS tracing, JSON logs

**Files:**
- Modify: `processor/pyproject.toml`, `processor/src/shortener_processor/{settings,telemetry,batch,main}.py`
- Modify: `processor/tests/conftest.py` (autouse logging restore), `processor/tests/unit/test_main.py`
- Create: `processor/tests/unit/test_batch_tracing.py`

**Interfaces:**
- Consumes: `configure_telemetry`, `span_link_from_traceparent` (Task 1); `ReceivedMessage.attributes["traceparent"]` (the API sends it from Task 2 on).
- Produces:
  - **`ProcessorSettings`** gains `otel_metric_export_interval_ms: int = 10_000` and `log_level: str = "INFO"`.
  - **`BatchProcessor`** gains the kw-only `tracer: Tracer | None = None` (default `trace.get_tracer(__name__)`). `process()` runs inside a span named **`process click batch`** with:
    - `links` = one link per message whose `traceparent` attribute is valid (malformed or absent ones are skipped)
    - attributes `messaging.system="aws_sqs"`, `messaging.destination.name="click-events"`, `messaging.batch.message_count=<n>`
    - an exception from the commit recorded on the span (status ERROR) and re-raised unchanged
  - **`Runtime`** gains `tracer_provider: TracerProvider | None = None` and `telemetry_shutdown: Callable[[], None] | None = None`. `serve()` calls `telemetry_shutdown` last in its `finally`.
  - **`build_runtime(settings, *, install_globals: bool = True)`** calls `configure_telemetry(service_name="shortener-click-processor", ...)`, instruments SQLAlchemy on the engine, and instruments botocore when `install_globals`.
  - **`telemetry.configure_meter_provider`** is removed. `main()` no longer calls `logging.basicConfig`, because `configure_telemetry` owns logging.

- [ ] **Step 1: Dependencies and logging-safe tests**

In `processor/pyproject.toml`:
- Replace the three `opentelemetry-*` lines with:
```toml
  "shortener-observability",
  "opentelemetry-api==1.45.0",
  "opentelemetry-sdk==1.45.0",
  "opentelemetry-instrumentation-sqlalchemy==0.66b0",
  "opentelemetry-instrumentation-botocore==0.66b0",
```
- Add `shortener-observability = { workspace = true }` to `[tool.uv.sources]`.

Then run `uv lock && uv sync --all-packages`.

Add the same autouse `_restore_logging` fixture as Task 2 to `processor/tests/conftest.py`, with `import logging` and `from collections.abc import Iterator` at the top.

- [ ] **Step 2: Write the failing tests**

`processor/tests/unit/test_batch_tracing.py`:
```python
from datetime import UTC, datetime
from uuid import UUID

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from shortener_events import ClickEvent, encode
from shortener_processor.batch import BatchProcessor
from shortener_processor.queue import ReceivedMessage
from shortener_processor.rollup_store import CommitResult
from shortener_processor.telemetry import ProcessorTelemetry

LINK = UUID("00000000-0000-0000-0000-00000000000a")
PRODUCER_A = ("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7")
PRODUCER_B = ("0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331")


def message(n: int, traceparent: str | None) -> ReceivedMessage:
    event = ClickEvent(event_id=f"e{n}", occurred_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
                       source="api", code="aaaaaaa", link_id=LINK)  # fmt: skip
    encoded = encode(event, traceparent=traceparent)
    return ReceivedMessage(f"m{n}", f"r{n}", encoded.body, encoded.attributes)


def tp(ids: tuple[str, str]) -> str:
    return f"00-{ids[0]}-{ids[1]}-01"


class Queue:
    async def receive(self, max_messages, wait_seconds):
        return []

    async def delete(self, receipt_handles):
        return []

    async def depth(self, queue_name):
        return 0


class Resolver:
    async def resolve(self, codes):
        return {}


class Store:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def commit(self, deltas, now):
        if self.error:
            raise self.error
        return CommitResult(deltas.link_ids, frozenset())


@pytest.fixture
def spans():
    return InMemorySpanExporter()


@pytest.fixture
def make(spans, meter):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))

    def _make(store=None):
        return BatchProcessor(Queue(), Resolver(), store or Store(), ProcessorTelemetry(meter),
                              tracer=provider.get_tracer("t"))  # fmt: skip

    return _make


async def test_batch_span_links_to_each_producer(make, spans):
    await make().process([message(1, tp(PRODUCER_A)), message(2, tp(PRODUCER_B)), message(3, None)])
    [span] = spans.get_finished_spans()
    assert span.name == "process click batch"
    linked = {(f"{l.context.trace_id:032x}", f"{l.context.span_id:016x}") for l in span.links}
    assert linked == {PRODUCER_A, PRODUCER_B}
    assert span.parent is None  # links, not a parent: a batch has many producers
    assert span.attributes["messaging.batch.message_count"] == 3
    assert span.attributes["messaging.system"] == "aws_sqs"


async def test_malformed_traceparent_is_ignored(make, spans):
    outcome = await make().process([message(1, "garbage"), message(2, "00-zz-yy-01")])
    assert outcome.ok == 2
    [span] = spans.get_finished_spans()
    assert span.links == ()


async def test_commit_failure_is_recorded_on_the_span(make, spans):
    with pytest.raises(ConnectionError):
        await make(Store(error=ConnectionError("db down"))).process([message(1, tp(PRODUCER_A))])
    [span] = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert any(e.name == "exception" for e in span.events)
```
Rename the comprehension variable `l` to `link` if ruff flags E741.

In `processor/tests/unit/test_main.py`:
- Delete `test_meter_provider_resource` and its `configure_meter_provider` import.
- Change `test_build_runtime_without_network` to `build_runtime(settings, install_globals=False)` and add:
```python
    assert built.tracer_provider is not None
    assert built.tracer_provider.resource.attributes["service.name"] == "shortener-click-processor"
    assert built.telemetry_shutdown is not None
    built.telemetry_shutdown()
```

Run: `uv run pytest processor/tests/unit -q`
Expected: FAIL. `BatchProcessor` rejects `tracer=`, and `build_runtime` rejects `install_globals`.

- [ ] **Step 3: Implement**

`settings.py`: add
```python
    otel_metric_export_interval_ms: int = Field(default=10_000, ge=1_000)
    log_level: str = "INFO"
```

`telemetry.py`: delete `configure_meter_provider` and the imports only it used.

`batch.py`:
- Add the imports `from opentelemetry import trace`, `from opentelemetry.trace import Tracer`, and `from shortener_observability import span_link_from_traceparent`.
- Add the kw-only `tracer: Tracer | None = None` to `__init__`, storing `self._tracer = tracer or trace.get_tracer(__name__)`.
- Rename the existing `process` body to `async def _process(self, messages)`, and add:
```python
    async def process(self, messages: Sequence[ReceivedMessage]) -> BatchOutcome:
        links = [
            link
            for message in messages
            if (link := span_link_from_traceparent(message.attributes.get("traceparent"))) is not None
        ]
        with self._tracer.start_as_current_span(
            "process click batch",
            links=links,
            attributes={
                "messaging.system": "aws_sqs",
                "messaging.destination.name": "click-events",
                "messaging.batch.message_count": len(messages),
            },
        ):
            return await self._process(messages)
```
`start_as_current_span` records the exception and sets ERROR status by default. Keep the empty-batch early return inside `_process`, so an empty batch still produces a span with count 0. That's harmless, and the consumer never passes empty batches anyway.

`main.py`:
- Add the imports:
```python
from collections.abc import Callable

from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider

from shortener_observability import configure_telemetry
```
- Remove the `configure_meter_provider` import.
- Add these fields to `Runtime`:
```python
    tracer_provider: TracerProvider | None = None
    telemetry_shutdown: Callable[[], None] | None = None
```
- In `build_runtime(settings, *, install_globals: bool = True)`, replace the telemetry line with:
```python
    telemetry = configure_telemetry(
        service_name="shortener-click-processor",
        service_version=settings.service_version,
        environment=settings.deployment_environment,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        metric_export_interval_ms=settings.otel_metric_export_interval_ms,
        log_level=settings.log_level,
        install_globals=install_globals,
    )
    processor_telemetry = ProcessorTelemetry(telemetry.meter("shortener_processor"))
```
  - After creating `engine`, add `SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=telemetry.tracer_provider)`.
  - After that, add `if install_globals: BotocoreInstrumentor().instrument(tracer_provider=telemetry.tracer_provider)`.
  - Pass `tracer=telemetry.tracer("shortener_processor")` to `BatchProcessor`.
  - Use `processor_telemetry` wherever the old `telemetry` object went.
  - Return `Runtime(..., telemetry=processor_telemetry, tracer_provider=telemetry.tracer_provider, telemetry_shutdown=telemetry.shutdown)`.
- In `serve()`'s `finally`, after `await runtime.engine.dispose()`:
```python
        if runtime.telemetry_shutdown is not None:
            runtime.telemetry_shutdown()
```
- In `main()`, delete `logging.basicConfig(level=logging.INFO)`.

Run: `uv run pytest processor -q`
Expected: all PASS, unit and integration.

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff format . && make check && git add processor uv.lock && git commit -m "feat(processor): batch span linked to producers, DB/SQS tracing, JSON logs

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 4: Admin UI — tracing into the API, `trace_id` on error pages, no secrets in telemetry

**Files:**
- Modify: `admin/pyproject.toml`, `admin/src/shortener_admin/{settings,deps,api_client,views,main}.py`, `admin/src/shortener_admin/templates/{error,login_failed}.html`
- Modify: `admin/tests/conftest.py` (autouse logging restore), `admin/tests/unit/test_main.py`
- Create: `admin/tests/unit/test_tracing.py`

**Interfaces:**
- Consumes: `configure_telemetry`, `current_trace_id` (Task 1).
- Produces:
  - **`AdminSettings`** gains `otel_exporter_otlp_endpoint: str | None = None`, `deployment_environment: str = "local"`, `otel_metric_export_interval_ms: int = 10_000` and `log_level: str = "INFO"`.
  - **`AdminDeps`** gains the optional fields `tracer_provider: TracerProvider | None = None`, `meter_provider: MeterProvider | None = None` and `telemetry_shutdown: Callable[[], None] | None = None`. The lifespan calls `telemetry_shutdown` after `aclose`.
  - **`ApiClient.http_client`** (a property) returns the underlying `httpx.AsyncClient`.
  - **`create_app`:** when `tracer_provider` is set, it instruments FastAPI (`excluded_urls="healthz,readyz,static"`) and the API client (`HTTPXClientInstrumentor.instrument_client`). Outgoing API calls then carry `traceparent`.
  - **`build_deps(settings, *, install_globals: bool = True)`** calls `configure_telemetry(service_name="shortener-admin", ...)` and instruments SQLAlchemy on the session engine.
  - **`views.render`** always adds `trace_id=current_trace_id()` to the template context. `error.html` and `login_failed.html` show `Reference: <code>{{ trace_id }}</code>` when it's set.

Keycloak calls made by Authlib's own httpx clients aren't traced. Instrumenting httpx globally would duplicate spans on the API client, and spec §10 requires only admin → API → Postgres. Record this in the report.

- [ ] **Step 1: Dependencies and logging-safe tests**

In `admin/pyproject.toml`, add to `dependencies`:
```toml
  "shortener-observability",
  "opentelemetry-api==1.45.0",
  "opentelemetry-sdk==1.45.0",
  "opentelemetry-instrumentation-fastapi==0.66b0",
  "opentelemetry-instrumentation-sqlalchemy==0.66b0",
  "opentelemetry-instrumentation-httpx==0.66b0",
```
and add:
```toml
[tool.uv.sources]
shortener-observability = { workspace = true }
```
Then run `uv lock && uv sync --all-packages`.

Add the same autouse `_restore_logging` fixture as Task 2 to `admin/tests/conftest.py`, with the imports at the top.

- [ ] **Step 2: Write the failing tests**

`admin/tests/unit/test_tracing.py`:
```python
import dataclasses
import logging

import httpx
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
INCOMING = {"traceparent": f"00-{TRACE}-00f067aa0ba902b7-01"}
SUMMARY = {"link_count": 0, "clicks_7d": 0, "top_links": [], "data_as_of": None}


@pytest.fixture
def spans() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def deps(deps, spans):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    return dataclasses.replace(deps, tracer_provider=provider, meter_provider=MeterProvider())


async def test_api_calls_carry_the_request_trace(client, mocks, ids, login_as):
    await login_as()
    route = mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(json=SUMMARY)
    assert (await client.get("/", headers=INCOMING)).status_code == 200
    sent = route.calls.last.request.headers["traceparent"]
    assert sent.startswith(f"00-{TRACE}-")


async def test_error_page_shows_the_request_trace_id(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").mock(side_effect=httpx.ConnectError("down"))
    response = await client.get("/", headers=INCOMING)
    assert response.status_code == 503
    assert f"<code>{TRACE}</code>" in response.text


async def test_failed_login_page_shows_the_request_trace_id(client, mocks):
    response = await client.get("/auth/callback", params={"code": "c", "state": "forged"}, headers=INCOMING)
    assert response.status_code == 400
    assert f"<code>{TRACE}</code>" in response.text


async def test_instrumented_pages_always_show_a_reference(client, mocks):
    response = await client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert "Reference:" in response.text


async def test_spans_and_logs_carry_no_secrets(client, mocks, ids, login_as, spans, caplog, settings):
    session = await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(json=SUMMARY)
    caplog.set_level(logging.DEBUG)
    await client.get("/", headers=INCOMING)
    await client.get("/auth/callback", params={"code": "c", "state": "forged"})
    secrets = [session.tokens.access_token, session.id, session.csrf_token,
               settings.oidc_client_secret.get_secret_value()]  # fmt: skip
    attribute_values = [str(v) for s in spans.get_finished_spans() for v in s.attributes.values()]
    for secret in secrets:
        assert not any(secret in value for value in attribute_values), secret
        assert secret not in caplog.text, secret
```
In `admin/tests/unit/test_auth_routes.py`, the app is uninstrumented (no tracer provider, so there's no span). Add one line to the end of the existing `test_failed_callback_renders_400_and_creates_no_session`:
```python
    assert "Reference:" not in response.text
```

In `admin/tests/unit/test_main.py`, change `test_build_deps_uses_real_components_without_network` to `build_deps(settings, install_globals=False)` and add:
```python
    assert deps.tracer_provider is not None
    assert deps.tracer_provider.resource.attributes["service.name"] == "shortener-admin"
    assert deps.telemetry_shutdown is not None
    deps.telemetry_shutdown()
```

Run: `uv run pytest admin/tests/unit -q`
Expected: FAIL (`dataclasses.replace` with unknown fields; no `Reference:` in pages; `install_globals` keyword).

- [ ] **Step 3: Implement**

`settings.py` (`AdminSettings`): add
```python
    otel_exporter_otlp_endpoint: str | None = None
    deployment_environment: str = "local"
    otel_metric_export_interval_ms: int = Field(default=10_000, ge=1_000)
    log_level: str = "INFO"
```

`deps.py`: add the imports `from opentelemetry.sdk.metrics import MeterProvider` and `from opentelemetry.sdk.trace import TracerProvider`, and add to `AdminDeps`:
```python
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    telemetry_shutdown: Callable[[], None] | None = None
```

`api_client.py`: add
```python
    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http
```

`views.py`: add `from shortener_observability import current_trace_id`, and change the context line in `render` to:
```python
    context = {
        "session": getattr(request.state, "session", None),
        "trace_id": current_trace_id(),
        **context,
    }
```

Templates. In both `error.html` and `login_failed.html`, add before `{% endblock %}`, inside the `<article>`:
```html
  {% if trace_id %}<p class="muted"><small>Reference: <code>{{ trace_id }}</code></small></p>{% endif %}
```

`main.py`:
- Add the imports:
```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from shortener_observability import configure_telemetry
```
- In the lifespan's `finally`, after `aclose`:
```python
        if deps.telemetry_shutdown is not None:
            deps.telemetry_shutdown()
```
- At the end of `create_app`, before `return app`:
```python
    if deps.tracer_provider is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=deps.tracer_provider,
            meter_provider=deps.meter_provider,
            excluded_urls="healthz,readyz,static",
        )
        HTTPXClientInstrumentor.instrument_client(
            deps.api.http_client, tracer_provider=deps.tracer_provider
        )
```
- In `build_deps(settings, *, install_globals: bool = True)`:
  - First, call `telemetry = configure_telemetry(service_name="shortener-admin", service_version=settings.service_version, environment=settings.deployment_environment, otlp_endpoint=settings.otel_exporter_otlp_endpoint, metric_export_interval_ms=settings.otel_metric_export_interval_ms, log_level=settings.log_level, install_globals=install_globals)`.
  - After creating the engine, call `SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=telemetry.tracer_provider)`.
  - Pass `tracer_provider=telemetry.tracer_provider, meter_provider=telemetry.meter_provider, telemetry_shutdown=telemetry.shutdown` to `AdminDeps`.
  - Keep everything else exactly as it is on `main`.

Run: `uv run pytest admin -q`
Expected: all PASS.

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff format . && make check && git add admin uv.lock && git commit -m "feat(admin): tracing into the API, trace_id on error pages, JSON logs

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 5: The "Shortener Overview" dashboard (dashboard-as-code) and its provisioning

**Files:**
- Create: `scripts/gen-dashboard` (uv-shebang, stdlib only), `infra/otel/dashboards/shortener-overview.json` (generated, committed), `infra/otel/grafana/shortener-dashboards.yaml`
- Modify: `docker-compose.yml` (`otel-lgtm` mounts; `admin` OTEL env)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Produces:
  - **The dashboard:** Grafana uid `shortener-overview`, title "Shortener Overview", provisioned from `/otel-lgtm/shortener-dashboards` into the "Shortener" folder. Every panel uses datasource uid `prometheus`.
  - **`scripts/gen-dashboard`:**
    - writes the JSON
    - `scripts/gen-dashboard --check` exits 1 when the committed file differs from the generator's output
  - **Panels** (spec §10), all built from the metric names verified against the live Prometheus:
    - stats: links created and blocked over the range; click-queue and DLQ depth
    - redirect rate by result
    - redirect latency p50/p95/p99
    - HTTP 5xx rate by service
    - click events published and dropped by reason
    - processed messages by result
    - event lag p95
    - queue depth by queue
    - batch duration p95 and publisher buffer

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard.py`:
```python
"""The provisioned dashboard: structure, metric-name allowlist (spec §10), generator drift."""

import json
import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = REPO_ROOT / "infra/otel/dashboards/shortener-overview.json"
PROVIDER = REPO_ROOT / "infra/otel/grafana/shortener-dashboards.yaml"
# Verified against the running Prometheus (OTLP → `_total` / `_seconds` suffixes).
KNOWN_METRICS = {
    "shortener_redirects_total", "shortener_redirect_duration_seconds_bucket",
    "shortener_links_created_total", "shortener_links_blocked_total",
    "shortener_click_events_published_total", "shortener_click_events_dropped_total",
    "shortener_click_events_buffer_size", "shortener_processor_messages_total",
    "shortener_processor_batch_duration_seconds_bucket", "shortener_processor_event_lag_seconds_bucket",
    "shortener_queue_depth", "http_server_request_duration_seconds_count",
}  # fmt: skip
REQUIRED_TITLES = {
    "Redirect rate by result", "Redirect latency (p50 / p95 / p99)", "HTTP 5xx rate by service",
    "Click events published / dropped", "Processed messages by result",
    "Event lag p95 (occurred → committed)", "Queue depth", "Links created (range)", "Links blocked (range)",
    "DLQ depth",
}  # fmt: skip
METRIC = re.compile(r"\b(?:shortener|http_server)_[a-z_]+\b")


def dashboard() -> dict:
    return json.loads(DASHBOARD.read_text())


def test_identity():
    board = dashboard()
    assert (board["uid"], board["title"]) == ("shortener-overview", "Shortener Overview")


def test_required_panels_are_present():
    assert REQUIRED_TITLES <= {panel["title"] for panel in dashboard()["panels"]}


def test_every_query_uses_prometheus_and_known_metrics():
    for panel in dashboard()["panels"]:
        assert panel["datasource"] == {"type": "prometheus", "uid": "prometheus"}, panel["title"]
        for target in panel["targets"]:
            names = set(METRIC.findall(target["expr"]))
            assert names and names <= KNOWN_METRICS, (panel["title"], names - KNOWN_METRICS)


def test_no_per_link_labels_in_queries():
    for panel in dashboard()["panels"]:
        for target in panel["targets"]:
            assert not re.search(r"\b(code|link_id)\b", target["expr"]), panel["title"]


def test_panel_ids_are_unique():
    ids = [panel["id"] for panel in dashboard()["panels"]]
    assert len(ids) == len(set(ids))


def test_provider_and_compose_mounts_agree():
    provider = yaml.safe_load(PROVIDER.read_text())["providers"][0]
    assert provider["options"]["path"] == "/otel-lgtm/shortener-dashboards"
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "./infra/otel/dashboards:/otel-lgtm/shortener-dashboards:ro" in compose
    assert (
        "./infra/otel/grafana/shortener-dashboards.yaml:"
        "/otel-lgtm/grafana/conf/provisioning/dashboards/shortener.yaml:ro"
    ) in compose


def test_committed_dashboard_matches_generator():
    result = subprocess.run(
        [str(REPO_ROOT / "scripts/gen-dashboard"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

Run: `uv run pytest tests/test_dashboard.py -q`
Expected: FAIL (the files don't exist).

- [ ] **Step 2: Write the generator and generate the dashboard**

`scripts/gen-dashboard` (then `chmod +x scripts/gen-dashboard`):
```python
#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Generate infra/otel/dashboards/shortener-overview.json (spec §10). `--check` verifies drift."""

import json
import sys
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).resolve().parents[1] / "infra/otel/dashboards/shortener-overview.json"
PROMETHEUS = {"type": "prometheus", "uid": "prometheus"}
RATE = "[$__rate_interval]"


def timeseries(id_: int, title: str, x: int, y: int, targets: list[tuple[str, str]], unit: str) -> dict[str, Any]:
    return {
        "id": id_, "type": "timeseries", "title": title, "datasource": PROMETHEUS,
        "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
        "targets": [{"refId": chr(65 + i), "expr": expr, "legendFormat": legend}
                    for i, (expr, legend) in enumerate(targets)],
    }  # fmt: skip


def stat(id_: int, title: str, x: int, expr: str) -> dict[str, Any]:
    return {
        "id": id_, "type": "stat", "title": title, "datasource": PROMETHEUS,
        "gridPos": {"h": 4, "w": 6, "x": x, "y": 0},
        "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}},
        "targets": [{"refId": "A", "expr": expr, "legendFormat": ""}],
    }  # fmt: skip


def quantile(q: str, bucket: str) -> str:
    return f"histogram_quantile({q}, sum by (le) (rate({bucket}{RATE})))"


def dashboard() -> dict[str, Any]:
    redirect = "shortener_redirect_duration_seconds_bucket"
    panels = [
        stat(1, "Links created (range)", 0, "sum(increase(shortener_links_created_total[$__range]))"),
        stat(2, "Links blocked (range)", 6, "sum(increase(shortener_links_blocked_total[$__range]))"),
        stat(3, "Click queue depth", 12, 'max(shortener_queue_depth{queue="click-events"})'),
        stat(4, "DLQ depth", 18, 'max(shortener_queue_depth{queue="click-events-dlq"})'),
        timeseries(5, "Redirect rate by result", 0, 4,
                   [(f"sum by (result) (rate(shortener_redirects_total{RATE}))", "{{result}}")], "reqps"),
        timeseries(6, "Redirect latency (p50 / p95 / p99)", 12, 4,
                   [(quantile(q, redirect), f"p{label}") for q, label in (("0.5", "50"), ("0.95", "95"), ("0.99", "99"))],
                   "s"),
        timeseries(7, "HTTP 5xx rate by service", 0, 12, [(
            'sum by (job) (rate(http_server_request_duration_seconds_count'
            '{job=~"shortener-.*", http_response_status_code=~"5.."}' + RATE + "))", "{{job}}")], "reqps"),
        timeseries(8, "Click events published / dropped", 12, 12, [
            (f"sum(rate(shortener_click_events_published_total{RATE}))", "published"),
            (f"sum by (reason) (rate(shortener_click_events_dropped_total{RATE}))", "dropped {{reason}}"),
        ], "ops"),
        timeseries(9, "Processed messages by result", 0, 20,
                   [(f"sum by (result) (rate(shortener_processor_messages_total{RATE}))", "{{result}}")], "ops"),
        timeseries(10, "Event lag p95 (occurred → committed)", 12, 20,
                   [(quantile("0.95", "shortener_processor_event_lag_seconds_bucket"), "p95")], "s"),
        timeseries(11, "Queue depth", 0, 28, [("max by (queue) (shortener_queue_depth)", "{{queue}}")], "short"),
        timeseries(12, "Batch duration p95 / publisher buffer", 12, 28, [
            (quantile("0.95", "shortener_processor_batch_duration_seconds_bucket"), "batch p95 (s)"),
            ("max(shortener_click_events_buffer_size)", "buffered events"),
        ], "short"),
    ]  # fmt: skip
    return {
        "uid": "shortener-overview", "title": "Shortener Overview", "tags": ["shortener"],
        "timezone": "utc", "schemaVersion": 39, "version": 1, "editable": True,
        "time": {"from": "now-1h", "to": "now"}, "refresh": "10s", "panels": panels,
    }  # fmt: skip


def main() -> int:
    rendered = json.dumps(dashboard(), indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv[1:]:
        if not OUTPUT.exists() or OUTPUT.read_text() != rendered:
            print(f"{OUTPUT} is stale; run scripts/gen-dashboard", file=sys.stderr)
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT}")
    return 0


sys.exit(main())
```
Run `scripts/gen-dashboard`. It writes the JSON.

`infra/otel/grafana/shortener-dashboards.yaml`:
```yaml
apiVersion: 1
providers:
  - name: "Shortener"
    folder: "Shortener"
    type: file
    disableDeletion: true
    allowUiUpdates: false
    options:
      path: /otel-lgtm/shortener-dashboards
```

In `docker-compose.yml`:
- Add to `otel-lgtm` `volumes:`:
```yaml
      - ./infra/otel/dashboards:/otel-lgtm/shortener-dashboards:ro
      - ./infra/otel/grafana/shortener-dashboards.yaml:/otel-lgtm/grafana/conf/provisioning/dashboards/shortener.yaml:ro
```
- Add to `admin` `environment:`:
```yaml
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-lgtm:4318
      DEPLOYMENT_ENVIRONMENT: local
```

Run:
```bash
uv run pytest tests/test_dashboard.py -q
docker compose up -d --wait otel-lgtm && curl -s http://localhost:3000/api/dashboards/uid/shortener-overview | python3 -c "import sys,json; print(json.load(sys.stdin)['dashboard']['title'])"
```
Expected: the tests pass, and the curl prints `Shortener Overview`. If Grafana doesn't pick up the provider, check the container logs (`docker compose logs otel-lgtm | grep -i provision`) and the mount paths. Don't change the tests' expected paths unless the image's provisioning directory really differs.

- [ ] **Step 3: Gates and commit**

```bash
uv run ruff format . && make check && git add scripts/gen-dashboard infra/otel tests/test_dashboard.py docker-compose.yml && git commit -m "feat(observability): provisioned Shortener Overview dashboard (dashboard-as-code)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```
`scripts/gen-dashboard` is linted as Python (ruff `extend-include = ["scripts/*"]`) and must have the executable bit (ruff `EXE`).

---

### Task 6: End-to-end observability checks, E2E in CI, docs

**Files:**
- Modify: `tests/e2e/test_observability.py` (add scenarios), `.github/workflows/ci.yml` (e2e job), `README.md`, `docs/superpowers/specs/2026-10-01-url-shortener-design.md`
- Test: the new e2e scenarios, plus the whole suite in CI

**Interfaces:**
- Consumes: everything above, and the running stack.
- Produces:
  - e2e scenarios that prove spec §10 end to end through Grafana's datasource proxy (Tempo, Loki, Prometheus)
  - a CI `e2e` job

- [ ] **Step 1: Rebuild the stack with the new code**

Run: `make up`
Expected: every service is healthy. The services now export traces and logs, and the admin exports too.

- [ ] **Step 2: Write the e2e scenarios**

Append to `tests/e2e/test_observability.py`. Merge these into its imports: `import base64`, `import json`, `import secrets`, `import subprocess`, `import time`, `from pathlib import Path`, and `from keycloak_tools.token import fetch_token`.
```python
REPO_ROOT = Path(__file__).resolve().parents[2]


def grafana(e2e_settings, path: str, **params) -> httpx.Response:
    return httpx.get(f"{e2e_settings.grafana_url}{path}", params=params, timeout=10)


def poll(check, *, timeout: float = 60.0, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = check()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s (last={last!r})")


def new_trace() -> tuple[str, str]:
    trace_id = secrets.token_hex(16)
    return trace_id, f"00-{trace_id}-{secrets.token_hex(8)}-01"


def as_hex(value: str) -> str:
    """Tempo returns ids as hex or as base64 depending on the endpoint; normalize to hex."""
    if len(value) == 32 and all(c in "0123456789abcdef" for c in value.lower()):
        return value.lower()
    return base64.b64decode(value).hex()


def resource_spans(trace_json: dict) -> list[dict]:
    return trace_json.get("batches") or trace_json.get("resourceSpans") or trace_json.get("trace", {}).get("resourceSpans", [])


def spans_with_service(trace_json: dict):
    for batch in resource_spans(trace_json):
        attrs = {a["key"]: a["value"].get("stringValue") for a in batch.get("resource", {}).get("attributes", [])}
        for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])):
            for span in scope.get("spans", []):
                yield attrs.get("service.name"), span


def test_dashboard_is_provisioned(e2e_settings):
    response = grafana(e2e_settings, "/api/dashboards/uid/shortener-overview")
    assert response.status_code == 200
    assert response.json()["dashboard"]["title"] == "Shortener Overview"


def test_service_http_metrics_reach_prometheus(e2e_settings):
    httpx.get(f"{e2e_settings.api_url}/api/v1/me", timeout=10)  # a 401 is still an HTTP server request

    def jobs():
        result = grafana(e2e_settings, "/api/datasources/proxy/uid/prometheus/api/v1/query",
                         query='count by (job) (http_server_request_duration_seconds_count{job=~"shortener-.*"})').json()
        return {series["metric"]["job"] for series in result["data"]["result"]} >= {"shortener-api"}

    poll(jobs)


def test_click_trace_links_into_the_processor(e2e_settings):
    token = fetch_token(e2e_settings.keycloak_url, "shortener", "eddie", "password")
    auth = {"Authorization": f"Bearer {token}"}
    link = httpx.post(f"{e2e_settings.api_url}/api/v1/links", json={"target_url": "https://example.com/trace"},
                      headers=auth, timeout=10).json()  # fmt: skip
    trace_id, traceparent = new_trace()
    try:
        redirect = httpx.get(f"{e2e_settings.api_url}/{link['code']}", headers={"traceparent": traceparent},
                             follow_redirects=False, timeout=10)  # fmt: skip
        assert redirect.status_code == 302

        def api_trace():
            response = grafana(e2e_settings, f"/api/datasources/proxy/uid/tempo/api/traces/{trace_id}")
            return response.status_code == 200 and any(
                service == "shortener-api" for service, _ in spans_with_service(response.json())
            )

        poll(api_trace)

        def linked_batch():
            now = int(time.time())
            search = grafana(e2e_settings, "/api/datasources/proxy/uid/tempo/api/search",
                             q='{resource.service.name="shortener-click-processor" && name="process click batch"}',
                             start=now - 600, end=now + 60, limit=50).json()  # fmt: skip
            for found in search.get("traces", []):
                body = grafana(e2e_settings, f"/api/datasources/proxy/uid/tempo/api/traces/{found['traceID']}").json()
                for service, span in spans_with_service(body):
                    if service == "shortener-click-processor" and any(
                        as_hex(l["traceId"]) == trace_id for l in span.get("links", [])
                    ):
                        return True
            return False

        poll(linked_batch, timeout=90, interval=3)
    finally:
        httpx.delete(f"{e2e_settings.api_url}/api/v1/links/{link['id']}", headers=auth, timeout=10)


def test_admin_log_line_is_correlated_in_loki(e2e_settings):
    trace_id, traceparent = new_trace()
    page = httpx.get(f"{e2e_settings.admin_url}/auth/callback", params={"code": "c", "state": "forged"},
                     headers={"traceparent": traceparent}, timeout=10)  # fmt: skip
    assert page.status_code == 400
    assert f"<code>{trace_id}</code>" in page.text  # user-visible reference

    def in_loki():
        now_ns = time.time_ns()
        result = grafana(e2e_settings, "/api/datasources/proxy/uid/loki/loki/api/v1/query_range",
                         query=f'{{service_name="shortener-admin"}} | trace_id="{trace_id}"',
                         start=now_ns - 600 * 10**9, end=now_ns + 60 * 10**9).json()  # fmt: skip
        return bool(result.get("data", {}).get("result"))

    poll(in_loki)
    stdout = subprocess.run(["docker", "compose", "logs", "--no-color", "--since", "10m", "admin"],
                            cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout  # fmt: skip
    lines = [json.loads(l[l.index("{"):]) for l in stdout.splitlines() if "{" in l and trace_id in l]
    assert any(line.get("trace_id") == trace_id and line.get("service") == "shortener-admin" for line in lines)
```
Notes:
- Rename the single-letter comprehension variables (`l`) if ruff flags E741.
- `E2ESettings` already has `grafana_url`, `api_url`, `admin_url` and `keycloak_url`.
- Tempo's search API and trace JSON shapes have changed across versions; `as_hex` and `resource_spans` absorb the known variants. If either poll times out, print one raw response and adapt **the parsing helper**. Never weaken what's asserted: the API span in the incoming trace, and a processor batch span linking to it.

Run: `make e2e`
Expected: every e2e test passes. If one of the four new scenarios fails, check the corresponding Grafana Explore view by hand before changing code.

- [ ] **Step 3: Run E2E in CI**

Add a job to `.github/workflows/ci.yml`:
```yaml
  e2e:
    runs-on: ubuntu-24.04
    needs: check
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.12.1"
          enable-cache: true
      - run: uv python install 3.12
      - run: make sync
      - run: cp .env.example .env
      - run: make up
      - run: make e2e
      - name: Compose logs
        if: failure()
        run: docker compose logs --no-color > compose-logs.txt
      - uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: compose-logs
          path: compose-logs.txt
```
Run: `uvx --from actionlint-py actionlint .github/workflows/ci.yml`
Expected: no output. (The job itself runs on GitHub once the repo has a remote; the default `.env.example` uses Keycloak on 8080, which is free on runners.)

- [ ] **Step 4: Docs**

In `README.md`, add an `## Observability` section after "Admin UI":
- **Grafana** at http://localhost:3000 → **Dashboards → Shortener → Shortener Overview**: redirects, latency, 5xx by service, and the click pipeline (published, dropped, processed, lag, queue and DLQ depth).
- **Traces** (Explore → Tempo):
  - one admin action is one trace across admin → API → Postgres
  - a click's processor batch span **links** to the redirect that produced it
  - error pages show a **Reference** (trace id); paste it into Tempo's trace search
- **Logs:** every service writes JSON lines with `trace_id`/`span_id` to stdout (`docker compose logs api`) and to Loki (Explore → Loki, e.g. `{service_name="shortener-api"} | trace_id="…"`).
- **Dashboard source:** `scripts/gen-dashboard` (dashboard-as-code). Run it after editing, and `make check` fails if the JSON is stale.

In the spec:
- **§10:**
  - After the "**SDK:**" bullet, add: "Setup is shared by all services through `libs/shortener-observability` (`configure_telemetry`, JSON logging, propagation helpers). Keycloak calls made by Authlib inside the admin service aren't traced."
  - In the **Dashboard** bullet, add: "Generated by `scripts/gen-dashboard` (dashboard-as-code); a test fails when the committed JSON is stale."
- **§13:** replace "E2E runs locally only for now." with "An `e2e` job brings up the full stack with `make up` and runs `make e2e`; compose logs are uploaded on failure."
- **§12 item 10:** replace it with "**Playwright UI tests** (real-browser checks of the admin UI; HTTP-level e2e can't see htmx behaviour)."

- [ ] **Step 5: Final gates and commit**

```bash
uv run ruff format . && make check && make e2e && git add tests/e2e .github/workflows/ci.yml README.md docs/superpowers/specs/2026-10-01-url-shortener-design.md && git commit -m "test(observability): e2e trace/log/metric checks; run e2e in CI; docs

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

## Plan 5 Done When

- `make up` brings up the stack, and the "Shortener Overview" dashboard is provisioned and populated.
- One admin page load is one trace across admin → API → Postgres in Tempo.
- A redirect's click event links the processor's batch span back to the redirect's trace.
- Every service logs JSON lines with `trace_id`/`span_id` to stdout and Loki. Error pages and 5xx problem responses carry the request's trace id.
- `make check` and `make e2e` pass locally, and CI has an `e2e` job, which actionlint accepts.
- Spec §10, §12 and §13 reflect the shared library, the dashboard-as-code, and E2E in CI.
