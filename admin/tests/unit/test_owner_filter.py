"""Investigating an owner: clickable usernames, the owner filter, and the owner summary."""

import httpx

LID = "11111111-1111-1111-1111-111111111111"
LINK = {
    "id": LID,
    "code": "aZ3kQ9x",
    "short_url": "http://localhost:8000/aZ3kQ9x",
    "target_url": "https://example.com/",
    "owner_username": "eddie",
    "status": "active",
    "is_active": True,
    "blocked_at": None,
    "blocked_reason": None,
    "created_at": "2026-10-01T10:00:00Z",
    "updated_at": "2026-10-01T10:00:00Z",
}


def page(items, total=None, page_no=1):
    total = len(items) if total is None else total
    return {"items": items, "total": total, "page": page_no, "page_size": 20}


def owner_counts(request: httpx.Request) -> httpx.Response:
    """The detail page asks for page_size=1 counts: all of eddie's links, then the blocked ones."""
    blocked = request.url.params.get("status") == "blocked"
    return httpx.Response(200, json=page([LINK], total=1 if blocked else 7))


def detail_routes(mocks, ids, link=LINK):
    mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(json=link)
    mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(500)
    mocks.get(f"{ids['API']}/api/v1/links/{LID}/events").respond(json=[])
    return mocks.get(f"{ids['API']}/api/v1/links").mock(side_effect=owner_counts)


async def test_owner_filter_is_passed_to_the_api(client, mocks, ids, login_as):
    await login_as("alice", ("admin",))
    route = mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([LINK]))
    await client.get("/links", params={"owner": " eddie ", "status": "blocked"})
    assert dict(route.calls.last.request.url.params) == {
        "owner": "eddie",
        "status": "blocked",
        "page": "1",
        "page_size": "20",
    }


async def test_owner_filter_shows_a_removable_chip_and_survives_paging(
    client, mocks, ids, login_as
):
    await login_as("alice", ("admin",))
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([LINK], total=45))
    html = (await client.get("/links", params={"owner": "eddie", "status": "blocked"})).text
    assert "Owner: <strong>eddie</strong>" in html
    assert 'href="/links?status=blocked">Clear owner' in html  # other filters kept
    assert '<input type="hidden" name="owner" value="eddie">' in html  # search keeps the filter
    assert 'href="/links?status=blocked&amp;owner=eddie&amp;page=2"' in html


async def test_no_chip_without_an_owner_filter(client, mocks, ids, login_as):
    await login_as("alice", ("admin",))
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([LINK]))
    html = (await client.get("/links")).text
    assert "Clear owner" not in html and 'name="owner"' not in html


async def test_owner_column_links_to_the_owner_filter(client, mocks, ids, login_as):
    await login_as("alice", ("admin",))
    odd = LINK | {"owner_username": "o'brien & co"}
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([odd]))
    html = (await client.get("/links")).text
    assert '<a href="/links?owner=o%27brien+%26+co">o&#39;brien &amp; co</a>' in html


async def test_detail_links_owner_and_summarises_their_links(client, mocks, ids, login_as):
    await login_as("alice", ("admin",))
    route = detail_routes(mocks, ids)
    html = (await client.get(f"/links/{LID}")).text
    assert '<a href="/links?owner=eddie">eddie</a>' in html
    assert '7 links · <a href="/links?owner=eddie&amp;status=blocked">1 blocked</a>' in html
    asked = [dict(call.request.url.params) for call in route.calls]
    assert asked == [
        {"owner": "eddie", "page": "1", "page_size": "1"},
        {"owner": "eddie", "status": "blocked", "page": "1", "page_size": "1"},
    ]


async def test_detail_without_owner_summary_when_the_api_fails(client, mocks, ids, login_as):
    await login_as("alice", ("admin",))
    mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(json=LINK)
    mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(500)
    mocks.get(f"{ids['API']}/api/v1/links/{LID}/events").respond(json=[])
    mocks.get(f"{ids['API']}/api/v1/links").respond(500)
    response = await client.get(f"/links/{LID}")
    assert response.status_code == 200
    assert '<a href="/links?owner=eddie">eddie</a>' in response.text
    assert "blocked</a>" not in response.text
