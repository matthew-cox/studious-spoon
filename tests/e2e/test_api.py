"""Full-stack scenario (spec §13 E2E, API part): real Keycloak tokens, real Postgres, real SQS."""

import time

import httpx
import pytest

from keycloak_tools.token import fetch_token

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


def test_readyz(api):
    assert api.get("/readyz").json() == {"status": "ok"}


def test_link_lifecycle(api, auth):
    created = api.post(
        "/api/v1/links", json={"target_url": "https://example.com/e2e"}, headers=auth("eddie")
    )
    assert created.status_code == 201, created.text
    link = created.json()
    url = f"/api/v1/links/{link['id']}"

    assert api.get(url, headers=auth("erin")).status_code == 404
    assert api.get(url, headers=auth("victor")).status_code == 200
    eddies = api.get(
        "/api/v1/links", params={"owner": "eddie", "page_size": 100}, headers=auth("alice")
    ).json()
    assert link["id"] in {i["id"] for i in eddies["items"]}
    assert {i["owner_username"] for i in eddies["items"]} == {"eddie"}
    forbidden = api.post(
        "/api/v1/links", json={"target_url": "https://x.example"}, headers=auth("victor")
    )
    assert forbidden.status_code == 403
    assert api.get("/api/v1/links", headers=auth("nora")).status_code == 403
    assert api.get("/api/v1/links").status_code == 401

    redirect = api.get(f"/{link['code']}", headers={"Referer": "https://ref.example/x"})
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "https://example.com/e2e"
    deadline = time.monotonic() + 30
    stats = {}
    while time.monotonic() < deadline:  # the click-processor rolls the event up asynchronously
        stats = api.get(f"{url}/stats", headers=auth("eddie")).json()
        if stats.get("total") == 1:
            break
        time.sleep(0.5)
    assert stats["total"] == 1
    assert stats["top_referrers"] == [{"referrer_host": "ref.example", "count": 1}]

    blocked = api.post(f"{url}/block", json={"reason": "e2e abuse test"}, headers=auth("alice"))
    assert blocked.status_code == 200
    assert api.patch(url, json={"is_active": True}, headers=auth("eddie")).status_code == 409
    assert api.get(f"/{link['code']}").status_code == 410
    assert api.get(f"{url}/events", headers=auth("eddie")).status_code == 403  # admins only

    assert api.delete(url, headers=auth("alice")).status_code == 204
    history = api.get(f"{url}/events", headers=auth("alice")).json()  # outlives the link
    assert [(e["action"], e["actor_username"], e["reason"]) for e in history] == [
        ("block", "alice", "e2e abuse test"),
        ("delete", "alice", None),
    ]


def test_support_moderates_but_cannot_change_links(api, auth):
    link = api.post(
        "/api/v1/links", json={"target_url": "https://example.com/report"}, headers=auth("eddie")
    ).json()
    url = f"/api/v1/links/{link['id']}"
    sam = auth("sam")

    assert api.get(url, headers=sam).status_code == 200
    assert (
        api.post("/api/v1/links", json={"target_url": "https://x.example"}, headers=sam).status_code
        == 403
    )
    assert api.patch(url, json={"is_active": False}, headers=sam).status_code == 403
    assert api.delete(url, headers=sam).status_code == 403

    assert api.post(f"{url}/block", json={"reason": "phishing"}, headers=sam).status_code == 200
    assert api.get(f"/{link['code']}").status_code == 410
    assert api.post(f"{url}/unblock", headers=sam).status_code == 200
    history = api.get(f"{url}/events", headers=sam).json()
    assert [(e["action"], e["actor_username"]) for e in history] == [
        ("block", "sam"),
        ("unblock", "sam"),
    ]

    assert api.delete(url, headers=auth("eddie")).status_code == 204
