import pytest

from shortener_api.publisher import BufferedClickPublisher
from shortener_api.telemetry import ApiTelemetry
from shortener_events import ClickEvent

pytestmark = pytest.mark.integration


async def test_active_link_redirects_and_publishes(client, deps, insert_link, clock, metric_value):
    link_id = insert_link(code="aZ3kQ9x", target_url="https://example.com/landing")
    response = await client.get(
        "/aZ3kQ9x", headers={"Referer": "https://news.example/item", "User-Agent": "UA/1.0"}
    )
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/landing"
    assert response.headers["cache-control"] == "private, no-store"
    [event] = deps.publisher.events
    assert event.code == "aZ3kQ9x"
    assert event.link_id == link_id
    assert event.source == "api"
    assert event.occurred_at == clock.now()
    assert event.referrer == "https://news.example/item"
    assert event.user_agent == "UA/1.0"
    assert metric_value("shortener.redirects", {"result": "ok"}) == 1
    assert metric_value("shortener.redirect.duration", {"result": "ok"}) == 1


async def test_each_click_gets_a_distinct_event_id(client, deps, insert_link):
    insert_link(code="twice01")
    await client.get("/twice01")
    await client.get("/twice01")
    assert len({e.event_id for e in deps.publisher.events}) == 2


@pytest.mark.parametrize("code", ["nope123", "aZ3kQ9X", "bad-code!", "x" * 33, "healthz2"])
async def test_unknown_or_malformed_codes_are_404_html(
    client, deps, insert_link, metric_value, code
):
    insert_link(code="aZ3kQ9x")
    response = await client.get(f"/{code}")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "private, no-store"
    assert deps.publisher.events == []
    assert metric_value("shortener.redirects", {"result": "not_found"}) == 1


async def test_disabled_link_is_404(client, deps, insert_link, metric_value):
    insert_link(code="off0001", is_active=False)
    assert (await client.get("/off0001")).status_code == 404
    assert deps.publisher.events == []
    assert metric_value("shortener.redirects", {"result": "disabled"}) == 1


async def test_blocked_link_is_410_even_if_active(client, deps, insert_link, clock, metric_value):
    insert_link(
        code="blk0003", blocked_at=clock.now(), blocked_by="sub-alice", blocked_reason="malware"
    )
    response = await client.get("/blk0003")
    assert response.status_code == 410
    assert "has been disabled" in response.text
    assert "malware" not in response.text  # the reason is for the owner, not the public
    assert response.headers["cache-control"] == "private, no-store"
    assert deps.publisher.events == []
    assert metric_value("shortener.redirects", {"result": "blocked"}) == 1


@pytest.mark.parametrize(
    ("path", "status"), [("/docs", 200), ("/openapi.json", 200), ("/healthz", 200)]
)
async def test_real_routes_are_not_shadowed(client, path, status):
    assert (await client.get(path)).status_code == status


async def test_redirect_still_works_when_buffer_is_full(
    client, deps, insert_link, meter, metric_value
):
    class NeverCalled:
        async def send(self, messages):
            raise AssertionError("no background loop in this test")

    deps.publisher = BufferedClickPublisher(NeverCalled(), ApiTelemetry(meter), maxsize=1)
    deps.publisher.publish(  # fill the only slot
        ClickEvent(event_id="x", occurred_at=deps.clock.now(), source="api", code="filler")
    )
    insert_link(code="full001")
    response = await client.get("/full001")
    assert response.status_code == 302
    assert metric_value("shortener.click_events.dropped", {"reason": "buffer_full"}) == 1


async def test_head_on_an_active_link_redirects_without_counting_a_click(
    client, deps, insert_link, metric_value
):
    insert_link(code="head001", target_url="https://example.com/preview")
    response = await client.head("/head001")
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/preview"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content == b""
    assert deps.publisher.events == []  # link previews and checkers are not clicks
    assert metric_value("shortener.redirects") == 0


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [({"is_active": False}, 404), ({"blocked_reason": "spam", "blocked_by": "sub-alice"}, 410)],
)
async def test_head_mirrors_get_status_for_unavailable_links(
    client, deps, insert_link, clock, kwargs, status
):
    if "blocked_by" in kwargs:
        kwargs["blocked_at"] = clock.now()
    insert_link(code="head002", **kwargs)
    response = await client.head("/head002")
    assert response.status_code == status
    assert response.content == b""
    assert (await client.head("/nope123")).status_code == 404
    assert deps.publisher.events == []
