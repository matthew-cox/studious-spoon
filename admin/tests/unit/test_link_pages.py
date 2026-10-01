import json

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


def link_route(mocks, ids, link=LINK):
    # From Task 8 on, the detail page also fetches stats; a 4xx there just omits the chart.
    mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(
        422, json={"title": "n/a", "status": 422}
    )
    return mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(json=link)


async def test_new_link_form_for_editors_only(client, login_as):
    await login_as()
    assert 'name="target_url"' in (await client.get("/links/new")).text
    await login_as("victor", ("viewer",))
    assert (await client.get("/links/new")).status_code == 403


async def test_create_posts_to_api_and_redirects_to_detail(client, mocks, ids, login_as):
    session = await login_as()
    route = mocks.post(f"{ids['API']}/api/v1/links").respond(201, json=LINK)
    response = await client.post(
        "/links", data={"csrf_token": session.csrf_token, "target_url": "https://example.com/"}
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/links/{LID}?created=1"
    assert json.loads(route.calls.last.request.content) == {"target_url": "https://example.com/"}


async def test_create_rejected_rerenders_form_with_message_and_value(client, mocks, ids, login_as):
    session = await login_as()
    mocks.post(f"{ids['API']}/api/v1/links").respond(
        422,
        json={
            "title": "Invalid target URL",
            "status": 422,
            "detail": "URL scheme must be http or https",
        },
    )
    response = await client.post(
        "/links", data={"csrf_token": session.csrf_token, "target_url": "ftp://x"}
    )
    assert response.status_code == 422
    assert (
        "URL scheme must be http or https" in response.text and 'value="ftp://x"' in response.text
    )


async def test_post_without_csrf_never_reaches_the_api(client, mocks, ids, login_as):
    await login_as()
    create = mocks.post(f"{ids['API']}/api/v1/links")
    delete = mocks.delete(f"{ids['API']}/api/v1/links/{LID}")
    assert (
        await client.post("/links", data={"target_url": "https://x.example"})
    ).status_code == 403
    assert (await client.post(f"/links/{LID}/delete")).status_code == 403
    assert not create.called and not delete.called


async def test_detail_shows_link_and_owner_controls(client, mocks, ids, login_as):
    await login_as()
    link_route(mocks, ids)
    html = (await client.get(f"/links/{LID}?created=1")).text
    assert "Short link created" in html
    assert 'data-copy="http://localhost:8000/aZ3kQ9x"' in html
    assert f'action="/links/{LID}/edit"' in html and f'action="/links/{LID}/delete"' in html
    assert f'action="/links/{LID}/block"' not in html  # editors don't moderate


async def test_viewer_sees_no_edit_controls(client, mocks, ids, login_as):
    await login_as("victor", ("viewer",))
    link_route(mocks, ids)
    html = (await client.get(f"/links/{LID}")).text
    assert f'action="/links/{LID}/edit"' not in html


async def test_detail_escapes_block_reason(client, mocks, ids, login_as):
    await login_as()
    link_route(
        mocks, ids, LINK | {"status": "blocked", "blocked_reason": "<script>alert('x')</script>"}
    )
    html = (await client.get(f"/links/{LID}")).text
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert f'action="/links/{LID}/edit"' not in html  # owners can't edit blocked links


async def test_non_uuid_id_is_a_404_page(client, mocks, ids, login_as):
    await login_as()
    response = await client.get("/links/not-a-uuid")
    assert response.status_code == 404
    assert "<html" in response.text


async def test_unknown_link_shows_api_404(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(
        404, json={"title": "Link not found", "status": 404}
    )
    response = await client.get(f"/links/{LID}")
    assert response.status_code == 404 and "Link not found" in response.text


async def test_edit_updates_target_and_redirects(client, mocks, ids, login_as):
    session = await login_as()
    route = mocks.patch(f"{ids['API']}/api/v1/links/{LID}").respond(json=LINK)
    response = await client.post(
        f"/links/{LID}/edit",
        data={"csrf_token": session.csrf_token, "target_url": "https://new.example/"},
    )
    assert response.headers["location"] == f"/links/{LID}?updated=1"
    assert json.loads(route.calls.last.request.content) == {"target_url": "https://new.example/"}


async def test_edit_on_blocked_link_shows_reason_inline(client, mocks, ids, login_as):
    session = await login_as()
    mocks.patch(f"{ids['API']}/api/v1/links/{LID}").respond(
        409,
        json={
            "title": "Link is blocked",
            "status": 409,
            "detail": "Blocked by an administrator: spam",
            "blocked_reason": "spam",
        },
    )
    link_route(mocks, ids, LINK | {"status": "blocked", "blocked_reason": "spam"})
    response = await client.post(
        f"/links/{LID}/edit",
        data={"csrf_token": session.csrf_token, "target_url": "https://x.example/"},
    )
    assert response.status_code == 409
    assert "Link is blocked: spam" in response.text


async def test_toggle_sends_is_active(client, mocks, ids, login_as):
    session = await login_as()
    route = mocks.patch(f"{ids['API']}/api/v1/links/{LID}").respond(json=LINK)
    await client.post(
        f"/links/{LID}/toggle", data={"csrf_token": session.csrf_token, "is_active": "false"}
    )
    assert json.loads(route.calls.last.request.content) == {"is_active": False}


async def test_delete_redirects_to_list(client, mocks, ids, login_as):
    session = await login_as()
    mocks.delete(f"{ids['API']}/api/v1/links/{LID}").respond(204)
    response = await client.post(f"/links/{LID}/delete", data={"csrf_token": session.csrf_token})
    assert response.headers["location"] == "/links?deleted=1"
