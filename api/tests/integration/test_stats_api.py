from datetime import UTC, date, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def eddies(insert_link):
    return insert_link(code="eddie01", owner_sub="sub-eddie", owner_username="eddie")


async def stats(client, link_id, headers, **params):
    return await client.get(f"/api/v1/links/{link_id}/stats", params=params, headers=headers)


async def test_default_hourly_series(client, token_for, eddies, insert_clicks, set_data_as_of):
    insert_clicks(eddies, utc(2026, 10, 1, 10), 3)
    insert_clicks(eddies, utc(2026, 10, 1, 11), 2)
    insert_clicks(eddies, utc(2026, 9, 20, 11), 99)  # outside the default 7 days
    set_data_as_of(utc(2026, 10, 1, 11, 59))
    body = (await stats(client, eddies, token_for("eddie"))).json()
    assert body["bucket"] == "hour"
    assert body["total"] == 5
    assert len(body["series"]) == 168
    assert body["series"][-2:] == [
        {"ts": "2026-10-01T10:00:00Z", "count": 3},
        {"ts": "2026-10-01T11:00:00Z", "count": 2},
    ]
    assert body["data_as_of"] == "2026-10-01T11:59:00Z"


async def test_daily_series_aggregates_hours(client, token_for, eddies, insert_clicks):
    insert_clicks(eddies, utc(2026, 9, 30, 23), 4)
    insert_clicks(eddies, utc(2026, 10, 1, 0), 1)
    insert_clicks(eddies, utc(2026, 10, 1, 5), 1)
    body = (
        await stats(
            client,
            eddies,
            token_for("eddie"),
            bucket="day",
            **{"from": "2026-09-29T00:00:00Z", "to": "2026-10-01T12:00:00Z"},
        )
    ).json()
    assert [(p["ts"][:10], p["count"]) for p in body["series"]] == [
        ("2026-09-29", 0),
        ("2026-09-30", 4),
        ("2026-10-01", 2),
    ]


async def test_top_referrers_ordered_limited_and_ranged(client, token_for, eddies, insert_referrer):
    for i in range(12):
        insert_referrer(eddies, date(2026, 9, 30), f"site{i:02}.example", i + 1)
    insert_referrer(eddies, date(2026, 9, 1), "old.example", 1000)
    body = (await stats(client, eddies, token_for("eddie"))).json()
    hosts = [r["referrer_host"] for r in body["top_referrers"]]
    assert len(hosts) == 10
    assert hosts[0] == "site11.example"
    assert "old.example" not in hosts


async def test_stats_visibility(client, token_for, eddies):
    assert (await stats(client, eddies, token_for("erin"))).status_code == 404
    assert (await stats(client, eddies, token_for("victor"))).status_code == 200
    assert (await stats(client, eddies, token_for("nora"))).status_code == 403


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-10-01T12:00:00Z", "to": "2026-10-01T12:00:00Z"},
        {"from": "2026-08-01T00:00:00Z", "to": "2026-10-01T00:00:00Z", "bucket": "hour"},
        {"bucket": "week"},
    ],
)
async def test_bad_ranges_are_422(client, token_for, eddies, params):
    assert (await stats(client, eddies, token_for("eddie"), **params)).status_code == 422


async def test_summary_is_scoped_and_ranked(client, token_for, insert_link, insert_clicks, clock):
    mine = insert_link(code="mine001", owner_sub="sub-eddie", owner_username="eddie")
    mine2 = insert_link(code="mine002", owner_sub="sub-eddie", owner_username="eddie")
    theirs = insert_link(code="their01", owner_sub="sub-erin", owner_username="erin")
    recent = clock.now() - timedelta(hours=2)
    insert_clicks(mine, recent, 5)
    insert_clicks(mine2, recent, 7)
    insert_clicks(mine2, clock.now() - timedelta(days=8), 100)  # older than 7 days
    insert_clicks(theirs, recent, 50)
    eddie = (await client.get("/api/v1/stats/summary", headers=token_for("eddie"))).json()
    assert eddie["link_count"] == 2
    assert eddie["clicks_7d"] == 12
    assert [t["code"] for t in eddie["top_links"]] == ["mine002", "mine001"]
    alice = (await client.get("/api/v1/stats/summary", headers=token_for("alice"))).json()
    assert alice["link_count"] == 3
    assert alice["clicks_7d"] == 62
    assert alice["top_links"][0]["code"] == "their01"
    assert (await client.get("/api/v1/stats/summary", headers=token_for("nora"))).status_code == 403
