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
