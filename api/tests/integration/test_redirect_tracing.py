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


async def test_redirect_trace_covers_postgres_and_reaches_the_event(
    client, deps, insert_link, spans
):
    insert_link(code="trace01")
    response = await client.get(
        "/trace01", headers={"traceparent": f"00-{TRACE}-00f067aa0ba902b7-01"}
    )
    assert response.status_code == 302
    finished = spans.get_finished_spans()
    assert any(s.attributes.get("db.system") == "postgresql" for s in finished)
    assert all(f"{s.context.trace_id:032x}" == TRACE for s in finished)
    [traceparent] = deps.publisher.traceparents
    assert traceparent is not None and traceparent.startswith(f"00-{TRACE}-")
