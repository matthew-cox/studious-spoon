import base64
import json
import secrets
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from keycloak_tools.token import fetch_token

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
    return (
        trace_json.get("batches")
        or trace_json.get("resourceSpans")
        or trace_json.get("trace", {}).get("resourceSpans", [])
    )


def spans_with_service(trace_json: dict):
    for batch in resource_spans(trace_json):
        attrs = {
            a["key"]: a["value"].get("stringValue")
            for a in batch.get("resource", {}).get("attributes", [])
        }
        for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])):
            for span in scope.get("spans", []):
                yield attrs.get("service.name"), span


def test_dashboard_is_provisioned(e2e_settings):
    response = grafana(e2e_settings, "/api/dashboards/uid/shortener-overview")
    assert response.status_code == 200
    assert response.json()["dashboard"]["title"] == "Shortener Overview"


def test_service_http_metrics_reach_prometheus(e2e_settings):
    httpx.get(
        f"{e2e_settings.api_url}/api/v1/me", timeout=10
    )  # a 401 is still an HTTP server request

    def jobs():
        result = grafana(
            e2e_settings,
            "/api/datasources/proxy/uid/prometheus/api/v1/query",
            query='count by (job) (http_server_duration_milliseconds_count{job=~"shortener-.*"})',
        ).json()
        return {series["metric"]["job"] for series in result["data"]["result"]} >= {"shortener-api"}

    poll(jobs)


def test_click_trace_links_into_the_processor(e2e_settings):
    token = fetch_token(e2e_settings.keycloak_url, "shortener", "eddie", "password")
    auth = {"Authorization": f"Bearer {token}"}
    link = httpx.post(
        f"{e2e_settings.api_url}/api/v1/links",
        json={"target_url": "https://example.com/trace"},
        headers=auth,
        timeout=10,
    ).json()
    trace_id, traceparent = new_trace()
    try:
        redirect = httpx.get(
            f"{e2e_settings.api_url}/{link['code']}",
            headers={"traceparent": traceparent},
            follow_redirects=False,
            timeout=10,
        )
        assert redirect.status_code == 302

        def api_trace():
            response = grafana(
                e2e_settings, f"/api/datasources/proxy/uid/tempo/api/traces/{trace_id}"
            )
            return response.status_code == 200 and any(
                service == "shortener-api" for service, _ in spans_with_service(response.json())
            )

        poll(api_trace)

        def linked_batch():
            now = int(time.time())
            search = grafana(
                e2e_settings,
                "/api/datasources/proxy/uid/tempo/api/search",
                q=(
                    '{resource.service.name="shortener-click-processor"'
                    ' && name="process click batch"}'
                ),
                start=now - 600,
                end=now + 60,
                limit=50,
            ).json()
            for found in search.get("traces", []):
                body = grafana(
                    e2e_settings, f"/api/datasources/proxy/uid/tempo/api/traces/{found['traceID']}"
                ).json()
                for service, span in spans_with_service(body):
                    if service == "shortener-click-processor" and any(
                        as_hex(ref["traceId"]) == trace_id for ref in span.get("links", [])
                    ):
                        return True
            return False

        poll(linked_batch, timeout=90, interval=3)
    finally:
        httpx.delete(f"{e2e_settings.api_url}/api/v1/links/{link['id']}", headers=auth, timeout=10)


def test_admin_log_line_is_correlated_in_loki(e2e_settings):
    trace_id, traceparent = new_trace()
    page = httpx.get(
        f"{e2e_settings.admin_url}/auth/callback",
        params={"code": "c", "state": "forged"},
        headers={"traceparent": traceparent},
        timeout=10,
    )
    assert page.status_code == 400
    assert f"<code>{trace_id}</code>" in page.text  # user-visible reference

    def in_loki():
        now_ns = time.time_ns()
        result = grafana(
            e2e_settings,
            "/api/datasources/proxy/uid/loki/loki/api/v1/query_range",
            query=f'{{service_name="shortener-admin"}} | trace_id="{trace_id}"',
            start=now_ns - 600 * 10**9,
            end=now_ns + 60 * 10**9,
        ).json()
        return bool(result.get("data", {}).get("result"))

    poll(in_loki)
    stdout = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "--since", "10m", "admin"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    lines = [
        json.loads(row[row.index("{") :])
        for row in stdout.splitlines()
        if "{" in row and trace_id in row
    ]
    assert any(
        line.get("trace_id") == trace_id and line.get("service") == "shortener-admin"
        for line in lines
    )
