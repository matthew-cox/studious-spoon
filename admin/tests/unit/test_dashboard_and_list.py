import httpx
import pytest

SUMMARY = {
    "link_count": 3,
    "clicks_7d": 42,
    "top_links": [
        {"id": "11111111-1111-1111-1111-111111111111", "code": "aZ3kQ9x", "clicks_7d": 30}
    ],
    "data_as_of": "2026-10-01T11:59:00Z",
}


def page(items, total=None, page_no=1):
    return {
        "items": items,
        "total": len(items) if total is None else total,
        "page": page_no,
        "page_size": 20,
    }


LINK = {
    "id": "11111111-1111-1111-1111-111111111111",
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


async def test_dashboard_shows_summary(client, mocks, ids, login_as):
    await login_as()
    route = mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(json=SUMMARY)
    html = (await client.get("/")).text
    assert 'id="link-count">3<' in html and 'id="clicks-7d">42<' in html
    assert 'href="/links/11111111-1111-1111-1111-111111111111">aZ3kQ9x</a>' in html
    assert "Data as of 2026-10-01 11:59:00 UTC" in html
    assert route.calls.last.request.headers["authorization"] == "Bearer access-eddie"


async def test_dashboard_before_any_processing(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(
        json={"link_count": 0, "clicks_7d": 0, "top_links": [], "data_as_of": None}
    )
    html = (await client.get("/")).text
    assert "No clicks in the last 7 days" in html and "No clicks processed yet" in html


async def test_dashboard_for_a_user_without_roles_never_calls_the_api(client, mocks, ids, login_as):
    await login_as("nora", ("offline_access",))
    route = mocks.get(f"{ids['API']}/api/v1/stats/summary")
    assert (await client.get("/")).status_code == 403
    assert not route.called


async def test_api_down_renders_503_page(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").mock(side_effect=httpx.ConnectError("down"))
    response = await client.get("/")
    assert response.status_code == 503
    assert "API unavailable" in response.text


async def test_links_full_page_and_htmx_partial(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([LINK]))
    full = (await client.get("/links")).text
    assert "<html" in full and 'id="links-table"' in full and 'name="q"' in full
    partial = (await client.get("/links", headers={"HX-Request": "true"})).text
    assert "<html" not in partial and partial.lstrip().startswith('<div id="links-table"')


async def test_filters_are_passed_and_bad_values_dropped(client, mocks, ids, login_as):
    await login_as()
    route = mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([]))
    await client.get("/links", params={"q": " exa ", "status": "blocked", "page": "2"})
    assert dict(route.calls.last.request.url.params) == {
        "q": "exa",
        "status": "blocked",
        "page": "2",
        "page_size": "20",
    }
    await client.get("/links", params={"status": "everything", "page": "-3"})
    assert dict(route.calls.last.request.url.params) == {"page": "1", "page_size": "20"}


async def test_pagination_links_keep_filters(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([LINK], total=45, page_no=2))
    html = (await client.get("/links", params={"q": "a b", "page": "2"})).text
    assert "Page 2 of 3" in html
    assert 'href="/links?q=a+b&amp;page=1"' in html and 'href="/links?q=a+b&amp;page=3"' in html


async def test_table_escapes_api_data(client, mocks, ids, login_as):
    await login_as()
    nasty = LINK | {
        "target_url": "https://x.example/<script>alert(1)</script>",
        "owner_username": "<b>eve</b>",
    }
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([nasty]))
    html = (await client.get("/links")).text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<b>eve</b>" not in html


async def test_empty_list_message(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([]))
    assert "No links match" in (await client.get("/links")).text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("²", "1"),
        ("٣", "1"),
        ("9" * 5000, "1"),
        ("0", "1"),
        ("-1", "1"),
        ("abc", "1"),
        ("1.5", "1"),
        ("3", "3"),
    ],
)
async def test_page_parsing_never_500s(client, mocks, ids, login_as, raw, expected):
    await login_as()
    route = mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([]))
    response = await client.get("/links", params={"page": raw})
    assert response.status_code == 200
    assert route.calls.last.request.url.params["page"] == expected
