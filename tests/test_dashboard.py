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
    assert {panel["title"] for panel in dashboard()["panels"]} >= REQUIRED_TITLES


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
