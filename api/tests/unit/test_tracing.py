import dataclasses

import httpx
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
    response = await client.get(
        "/aZ3kQ9x", headers=INCOMING
    )  # unit deps: database unreachable -> 503
    assert response.status_code == 503
    assert response.json()["trace_id"] == TRACE


async def test_4xx_problem_has_no_trace_id(client):
    response = await client.get("/api/v1/me", headers=INCOMING)
    assert response.status_code == 401
    assert "trace_id" not in response.json()


async def test_unhandled_500_problem_includes_trace_id(deps, token_for):
    from shortener_api.main import create_app

    class Exploding:
        async def principal(self, _token: str):
            raise RuntimeError("boom")

    exploding = dataclasses.replace(deps, token_validator=Exploding())
    transport = httpx.ASGITransport(app=create_app(exploding), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sho.rt") as http:
        response = await http.get("/api/v1/me", headers=token_for("eddie") | INCOMING)
    assert response.status_code == 500
    assert response.json()["trace_id"] == TRACE


async def test_traced_urls_are_anchored_not_substring_matches(client, spans):
    await client.get("/healthz")
    await client.get("/readyz")
    assert spans.get_finished_spans() == ()
    await client.get("/api/v1/healthzx")  # merely contains the word: still traced
    assert [s for s in spans.get_finished_spans() if s.kind.name == "SERVER"]


async def test_http_server_metrics_have_no_host_derived_attributes(deps, spans):
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from shortener_api.main import create_app
    from shortener_observability.setup import http_server_metric_views

    reader = InMemoryMetricReader()
    meters = MeterProvider(metric_readers=[reader], views=http_server_metric_views())
    app = create_app(dataclasses.replace(deps, meter_provider=meters))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    for host in ("evil-1.example", "evil-2.example:9999"):
        async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}") as http:
            assert (await http.get("/api/v1/me")).status_code == 401

    points = []
    for resource in reader.get_metrics_data().resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name.startswith("http.server"):
                    points += [(metric.name, dict(p.attributes)) for p in metric.data.data_points]
    assert points
    for _, attrs in points:
        assert not {k for k in attrs if "server_name" in k or "host" in k}, attrs
    durations = [a for n, a in points if n == "http.server.duration"]
    assert len(durations) == 1 and durations[0]["http.status_code"] == 401
