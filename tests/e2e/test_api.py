"""Full-stack scenario (spec §13 E2E, API part): real Keycloak tokens, real Postgres, real SQS."""

import boto3
import httpx
import pytest

from keycloak_tools.token import fetch_token
from shortener_events import SqsMessage, decode, from_sqs_attributes

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def auth(e2e_settings):
    def _headers(username: str) -> dict[str, str]:
        token = fetch_token(e2e_settings.keycloak_url, "shortener", username, "password")
        return {"Authorization": f"Bearer {token}"}

    return _headers


@pytest.fixture(scope="module")
def api(e2e_settings):
    with httpx.Client(base_url=e2e_settings.api_url, timeout=10, follow_redirects=False) as client:
        yield client


@pytest.fixture(scope="module")
def queue(e2e_settings):
    sqs = boto3.client(
        "sqs",
        endpoint_url=e2e_settings.sqs_endpoint_url,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    url = sqs.get_queue_url(QueueName="click-events")["QueueUrl"]
    sqs.purge_queue(QueueUrl=url)
    return sqs, url


def receive_click(sqs, url, code: str):
    for _ in range(5):  # long-poll up to ~10 s total; the publisher flushes every 250 ms
        for message in sqs.receive_message(
            QueueUrl=url, MaxNumberOfMessages=10, MessageAttributeNames=["All"], WaitTimeSeconds=2
        ).get("Messages", []):
            event = decode(
                SqsMessage(message["Body"], from_sqs_attributes(message["MessageAttributes"]))
            )
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
            if event.code == code:
                return event
    raise AssertionError(f"no click event for {code}")


def test_readyz(api):
    assert api.get("/readyz").json() == {"status": "ok"}


def test_link_lifecycle(api, auth, queue):
    created = api.post(
        "/api/v1/links", json={"target_url": "https://example.com/e2e"}, headers=auth("eddie")
    )
    assert created.status_code == 201, created.text
    link = created.json()
    url = f"/api/v1/links/{link['id']}"

    assert api.get(url, headers=auth("erin")).status_code == 404
    assert api.get(url, headers=auth("victor")).status_code == 200
    forbidden = api.post(
        "/api/v1/links", json={"target_url": "https://x.example"}, headers=auth("victor")
    )
    assert forbidden.status_code == 403
    assert api.get("/api/v1/links", headers=auth("nora")).status_code == 403
    assert api.get("/api/v1/links").status_code == 401

    redirect = api.get(f"/{link['code']}", headers={"Referer": "https://ref.example/x"})
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "https://example.com/e2e"
    event = receive_click(*queue, link["code"])
    assert str(event.link_id) == link["id"]
    assert event.referrer == "https://ref.example/x"

    blocked = api.post(f"{url}/block", json={"reason": "e2e abuse test"}, headers=auth("alice"))
    assert blocked.status_code == 200
    assert api.patch(url, json={"is_active": True}, headers=auth("eddie")).status_code == 409
    assert api.get(f"/{link['code']}").status_code == 410

    assert api.delete(url, headers=auth("alice")).status_code == 204
