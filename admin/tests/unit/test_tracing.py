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
CODE = "authcode-9f8e7d6c5b4a"
STATE = "forgedstate-1a2b3c4d5e6f"
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
    response = await client.get(
        "/auth/callback", params={"code": "c", "state": "forged"}, headers=INCOMING
    )
    assert response.status_code == 400
    assert f"<code>{TRACE}</code>" in response.text


async def test_instrumented_pages_always_show_a_reference(client, mocks):
    response = await client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert "Reference:" in response.text


async def test_spans_and_logs_carry_no_secrets(
    client, mocks, ids, login_as, spans, caplog, settings
):
    session = await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(json=SUMMARY)
    caplog.set_level(logging.DEBUG)
    await client.get("/", headers=INCOMING)
    await client.get("/auth/callback", params={"code": CODE, "state": STATE}, headers=INCOMING)
    secrets = [CODE, STATE, session.tokens.access_token, session.id, session.csrf_token,
               settings.oidc_client_secret.get_secret_value()]  # fmt: skip
    attribute_values = [str(v) for s in spans.get_finished_spans() for v in s.attributes.values()]
    for secret in secrets:
        assert not any(secret in value for value in attribute_values), secret
        # httpx logs the *test client's* own browser-side request URL; that is not app output.
        app_logs = "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx")
        assert secret not in app_logs, secret
        assert secret not in "".join(str(r.exc_info) for r in caplog.records), secret


async def test_unhandled_error_page_shows_the_request_trace_id(app, client):
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    response = await client.get("/boom", headers=INCOMING)
    assert response.status_code == 500
    assert f"<code>{TRACE}</code>" in response.text
