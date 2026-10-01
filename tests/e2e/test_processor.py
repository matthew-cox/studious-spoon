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
    created = api.post(
        "/api/v1/links", json={"target_url": "https://example.com/pipe"}, headers=eddie
    )
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
        assert (
            api.get(f"/{link['code']}", headers={"Referer": "https://ref.example/a"}).status_code
            == 302
        )
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
