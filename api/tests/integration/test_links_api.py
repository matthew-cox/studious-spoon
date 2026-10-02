import pytest

pytestmark = pytest.mark.integration


async def create(client, headers, url="https://example.com/page"):
    return await client.post("/api/v1/links", json={"target_url": url}, headers=headers)


async def test_editor_creates_a_link(client, token_for, metric_value):
    response = await create(client, token_for("eddie"))
    assert response.status_code == 201
    body = response.json()
    assert len(body["code"]) == 7
    assert body["short_url"] == f"http://sho.rt/{body['code']}"
    assert body["target_url"] == "https://example.com/page"
    assert body["owner_username"] == "eddie"
    assert body["status"] == "active"
    assert body["blocked_reason"] is None
    assert metric_value("shortener.links.created") == 1


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "http://sho.rt/abc", "https://u:p@x.example"]
)
async def test_invalid_target_is_422_problem(client, token_for, url):
    response = await create(client, token_for("eddie"), url)
    assert response.status_code == 422
    assert response.json()["title"] == "Invalid target URL"


@pytest.mark.parametrize(("user", "status"), [("victor", 403), ("nora", 403)])
async def test_non_editors_cannot_create(client, token_for, user, status):
    assert (await create(client, token_for(user))).status_code == status


async def test_no_token_is_401(client):
    assert (
        await client.post("/api/v1/links", json={"target_url": "https://x.example"})
    ).status_code == 401


async def test_missing_body_field_is_422_with_errors(client, token_for):
    response = await client.post("/api/v1/links", json={}, headers=token_for("eddie"))
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "target_url"]


async def test_code_collision_is_retried(client, deps, token_for, scripted_random):
    first = (await create(client, token_for("eddie"))).json()["code"]
    deps.rng = scripted_random([first, "Zz9Zz9Z"])
    response = await create(client, token_for("eddie"))
    assert response.status_code == 201
    assert response.json()["code"] == "Zz9Zz9Z"


async def test_code_exhaustion_is_500(client, deps, token_for, scripted_random):
    taken = (await create(client, token_for("eddie"))).json()["code"]
    deps.rng = scripted_random([taken] * 5)
    response = await create(client, token_for("eddie"))
    assert response.status_code == 500
    assert response.json()["title"] == "Could not allocate a short code"


async def test_listing_is_scoped_for_editors(client, token_for):
    await create(client, token_for("eddie"), "https://eddie.example/")
    await create(client, token_for("erin"), "https://erin.example/")
    eddie = (await client.get("/api/v1/links", headers=token_for("eddie"))).json()
    assert [i["owner_username"] for i in eddie["items"]] == ["eddie"]
    # The owner filter narrows an editor's own scope; it never widens it.
    others = await client.get("/api/v1/links?owner=erin", headers=token_for("eddie"))
    assert others.json()["items"] == []
    for user in ("alice", "victor"):
        everything = (await client.get("/api/v1/links", headers=token_for(user))).json()
        assert everything["total"] == 2
        only_erin = await client.get("/api/v1/links?owner=erin", headers=token_for(user))
        assert [i["owner_username"] for i in only_erin.json()["items"]] == ["erin"]


async def test_owner_filter_is_an_exact_username_match(client, token_for):
    await create(client, token_for("eddie"), "https://eddie.example/")
    await create(client, token_for("erin"), "https://eddie.example/erin")
    for owner in ("edd", "EDDIE", "eddie%"):
        response = await client.get(f"/api/v1/links?owner={owner}", headers=token_for("alice"))
        assert response.json()["total"] == 0, owner
    eddie = await client.get("/api/v1/links?owner=eddie", headers=token_for("alice"))
    assert [i["owner_username"] for i in eddie.json()["items"]] == ["eddie"]


async def test_owner_filter_combines_with_status(client, token_for):
    first = (await create(client, token_for("eddie"), "https://a.example/")).json()
    await create(client, token_for("eddie"), "https://b.example/")
    await client.post(
        f"/api/v1/links/{first['id']}/block", json={"reason": "spam"}, headers=token_for("alice")
    )
    blocked = await client.get(
        "/api/v1/links?owner=eddie&status=blocked&page_size=1", headers=token_for("alice")
    )
    assert blocked.json()["total"] == 1


async def test_listing_rejects_bad_paging(client, token_for):
    response = await client.get("/api/v1/links?page_size=101", headers=token_for("alice"))
    assert response.status_code == 422
    assert (await client.get("/api/v1/links?page=0", headers=token_for("alice"))).status_code == 422


async def test_nora_cannot_list(client, token_for):
    assert (await client.get("/api/v1/links", headers=token_for("nora"))).status_code == 403


async def test_get_hides_other_editors_links(client, token_for):
    link = (await create(client, token_for("erin"))).json()
    assert (
        await client.get(f"/api/v1/links/{link['id']}", headers=token_for("eddie"))
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/links/{link['id']}", headers=token_for("victor"))
    ).status_code == 200


async def test_unknown_and_malformed_ids_are_404_and_422(client, token_for):
    headers = token_for("alice")
    assert (
        await client.get("/api/v1/links/00000000-0000-0000-0000-000000000000", headers=headers)
    ).status_code == 404
    assert (await client.get("/api/v1/links/not-a-uuid", headers=headers)).status_code == 422


async def test_owner_updates_target_and_toggles_active(client, token_for):
    link = (await create(client, token_for("eddie"))).json()
    response = await client.patch(
        f"/api/v1/links/{link['id']}",
        json={"target_url": "https://changed.example/", "is_active": False},
        headers=token_for("eddie"),
    )
    assert response.status_code == 200
    assert response.json()["target_url"] == "https://changed.example/"
    assert response.json()["status"] == "disabled"


async def test_empty_patch_is_422(client, token_for):
    link = (await create(client, token_for("eddie"))).json()
    response = await client.patch(
        f"/api/v1/links/{link['id']}", json={}, headers=token_for("eddie")
    )
    assert response.status_code == 422


async def test_viewer_cannot_patch_and_other_editor_gets_404(client, token_for):
    link = (await create(client, token_for("eddie"))).json()
    url = f"/api/v1/links/{link['id']}"
    assert (
        await client.patch(url, json={"is_active": False}, headers=token_for("victor"))
    ).status_code == 403
    assert (
        await client.patch(url, json={"is_active": False}, headers=token_for("erin"))
    ).status_code == 404


async def test_owner_patch_on_blocked_link_is_409(client, token_for, insert_link, clock):
    link_id = insert_link(
        code="blk0001", owner_sub="sub-eddie", blocked_at=clock.now(), blocked_by="sub-alice",
        blocked_reason="phishing",
    )  # fmt: skip
    response = await client.patch(
        f"/api/v1/links/{link_id}",
        json={"target_url": "https://clean.example/"},
        headers=token_for("eddie"),
    )
    assert response.status_code == 409
    assert response.json()["blocked_reason"] == "phishing"
    admin = await client.patch(
        f"/api/v1/links/{link_id}", json={"is_active": False}, headers=token_for("alice")
    )
    assert admin.status_code == 200


async def test_delete_then_gone(client, token_for):
    link = (await create(client, token_for("eddie"))).json()
    url = f"/api/v1/links/{link['id']}"
    assert (await client.delete(url, headers=token_for("erin"))).status_code == 404
    assert (await client.delete(url, headers=token_for("eddie"))).status_code == 204
    assert (await client.get(url, headers=token_for("eddie"))).status_code == 404


async def test_owner_cannot_delete_blocked_link(client, token_for, insert_link, clock):
    link_id = insert_link(
        code="blk0002", owner_sub="sub-eddie", blocked_at=clock.now(), blocked_by="sub-alice",
        blocked_reason="malware",
    )  # fmt: skip
    assert (
        await client.delete(f"/api/v1/links/{link_id}", headers=token_for("eddie"))
    ).status_code == 409
