import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from shortener_admin.api_client import ApiClient, ApiError

TOKEN = "tok"


@pytest.fixture
async def api(ids):
    http = httpx.AsyncClient(base_url=ids["API"])
    yield ApiClient(http)
    await http.aclose()


@respx.mock
async def test_sends_bearer_token_and_returns_json(api, ids):
    route = respx.get(f"{ids['API']}/api/v1/me").respond(
        json={"sub": "s", "username": "u", "roles": []}
    )
    assert (await api.me(TOKEN))["username"] == "u"
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"


@respx.mock
async def test_list_links_passes_only_given_filters(api, ids):
    route = respx.get(f"{ids['API']}/api/v1/links").respond(
        json={"items": [], "total": 0, "page": 2, "page_size": 20}
    )
    await api.list_links(TOKEN, q="exa", page=2)
    assert dict(route.calls.last.request.url.params) == {"q": "exa", "page": "2", "page_size": "20"}


@respx.mock
async def test_update_sends_only_provided_fields(api, ids):
    route = respx.patch(f"{ids['API']}/api/v1/links/L1").respond(json={"id": "L1"})
    await api.update_link(TOKEN, "L1", is_active=False)
    assert json.loads(route.calls.last.request.content) == {"is_active": False}


@respx.mock
async def test_delete_returns_none_on_204(api, ids):
    respx.delete(f"{ids['API']}/api/v1/links/L1").respond(204)
    assert await api.delete_link(TOKEN, "L1") is None


@respx.mock
async def test_stats_sends_iso_range(api, ids):
    route = respx.get(f"{ids['API']}/api/v1/links/L1/stats").respond(json={"total": 0})
    await api.link_stats(TOKEN, "L1", bucket="day", start=datetime(2026, 9, 1, tzinfo=UTC))
    assert dict(route.calls.last.request.url.params) == {
        "bucket": "day",
        "from": "2026-09-01T00:00:00+00:00",
    }


@respx.mock
async def test_problem_json_becomes_api_error_with_extras(api, ids):
    respx.patch(f"{ids['API']}/api/v1/links/L1").respond(
        409,
        json={"type": "about:blank", "title": "Link is blocked", "status": 409,
              "detail": "Blocked by an administrator: spam", "blocked_reason": "spam"},
        headers={"content-type": "application/problem+json"},
    )  # fmt: skip
    with pytest.raises(ApiError) as caught:
        await api.update_link(TOKEN, "L1", target_url="https://x.example")
    error = caught.value
    assert (error.status, error.title, error.detail) == (
        409,
        "Link is blocked",
        "Blocked by an administrator: spam",
    )
    assert error.extra == {"blocked_reason": "spam"}


@respx.mock
async def test_non_json_error_falls_back_to_reason_phrase(api, ids):
    respx.get(f"{ids['API']}/api/v1/me").respond(502, text="<html>bad gateway</html>")
    with pytest.raises(ApiError) as caught:
        await api.me(TOKEN)
    assert (caught.value.status, caught.value.title) == (502, "Bad Gateway")


@respx.mock
async def test_transport_failure_is_503(api, ids):
    respx.get(f"{ids['API']}/api/v1/stats/summary").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ApiError) as caught:
        await api.summary(TOKEN)
    assert caught.value.status == 503
    assert caught.value.title == "API unavailable"


@respx.mock
async def test_block_and_create_bodies(api, ids):
    block = respx.post(f"{ids['API']}/api/v1/links/L1/block").respond(json={"id": "L1"})
    create = respx.post(f"{ids['API']}/api/v1/links").respond(201, json={"id": "L2"})
    await api.block_link(TOKEN, "L1", "phishing")
    await api.create_link(TOKEN, "https://example.com")
    assert json.loads(block.calls.last.request.content) == {"reason": "phishing"}
    assert json.loads(create.calls.last.request.content) == {"target_url": "https://example.com"}
