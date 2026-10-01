import html as html_lib
import json
import re
from datetime import timedelta

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
STATS = {
    "total": 5,
    "bucket": "hour",
    "from": "2026-10-01T10:00:00Z",
    "to": "2026-10-01T12:00:00Z",
    "series": [
        {"ts": "2026-10-01T10:00:00Z", "count": 3},
        {"ts": "2026-10-01T11:00:00Z", "count": 2},
    ],
    "top_referrers": [
        {"referrer_host": "news.example", "count": 4},
        {"referrer_host": "<b>x</b>", "count": 1},
    ],
    "data_as_of": "2026-10-01T11:59:00Z",
}


def detail_routes(mocks, ids, link=LINK, stats=STATS):
    mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(json=link)
    return mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(json=stats)


async def test_admin_sees_block_form_and_blocks_with_reason(client, mocks, ids, login_as):
    session = await login_as("alice", ("admin",))
    detail_routes(mocks, ids)
    assert f'action="/links/{LID}/block"' in (await client.get(f"/links/{LID}")).text
    route = mocks.post(f"{ids['API']}/api/v1/links/{LID}/block").respond(json=LINK)
    response = await client.post(
        f"/links/{LID}/block", data={"csrf_token": session.csrf_token, "reason": "phishing"}
    )
    assert response.headers["location"] == f"/links/{LID}?updated=1"
    assert json.loads(route.calls.last.request.content) == {"reason": "phishing"}


async def test_double_block_shows_conflict_inline(client, mocks, ids, login_as):
    session = await login_as("alice", ("admin",))
    detail_routes(mocks, ids)
    mocks.post(f"{ids['API']}/api/v1/links/{LID}/block").respond(
        409, json={"title": "Link is already blocked", "status": 409}
    )
    response = await client.post(
        f"/links/{LID}/block", data={"csrf_token": session.csrf_token, "reason": "again"}
    )
    assert response.status_code == 409 and "Link is already blocked" in response.text


async def test_unblock(client, mocks, ids, login_as):
    session = await login_as("alice", ("admin",))
    route = mocks.post(f"{ids['API']}/api/v1/links/{LID}/unblock").respond(json=LINK)
    response = await client.post(f"/links/{LID}/unblock", data={"csrf_token": session.csrf_token})
    assert response.status_code == 303 and route.called


async def test_detail_renders_chart_referrers_and_data_as_of(client, mocks, ids, login_as):
    await login_as()
    detail_routes(mocks, ids)
    page = (await client.get(f"/links/{LID}")).text
    assert "<strong>5</strong> clicks" in page
    assert "Data as of 2026-10-01 11:59:00 UTC" in page
    assert "news.example" in page and "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


async def test_chart_data_attribute_is_escaped(client, mocks, ids, login_as):
    await login_as()
    detail_routes(mocks, ids)
    page = (await client.get(f"/links/{LID}")).text
    [raw] = re.findall(
        r"data-chart='([^']*)'", page
    )  # attribute value contains no raw single quote
    assert json.loads(html_lib.unescape(raw)) == {
        "labels": ["10-01 10:00", "10-01 11:00"],
        "counts": [3, 2],
    }


async def test_stats_partial_for_htmx_with_day_bucket(client, mocks, ids, login_as, clock):
    await login_as()
    route = mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(
        json=STATS | {"bucket": "day"}
    )
    response = await client.get(
        f"/links/{LID}/stats", params={"bucket": "day"}, headers={"HX-Request": "true"}
    )
    assert response.text.lstrip().startswith('<div id="stats"')
    params = dict(route.calls.last.request.url.params)
    assert params["bucket"] == "day"
    assert params["from"] == (clock() - timedelta(days=30)).isoformat()


async def test_stats_without_htmx_redirects_to_detail(client, login_as):
    await login_as()
    response = await client.get(f"/links/{LID}/stats", params={"bucket": "nonsense"})
    assert (
        response.status_code == 303 and response.headers["location"] == f"/links/{LID}?bucket=hour"
    )


async def test_stats_failure_does_not_break_the_detail_page(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(json=LINK)
    mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(
        422, json={"title": "Invalid range", "status": 422}
    )
    response = await client.get(f"/links/{LID}")
    assert response.status_code == 200 and 'id="stats"' not in response.text
