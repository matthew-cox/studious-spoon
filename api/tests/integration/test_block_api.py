import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def eddies_link(client, token_for):
    response = await client.post(
        "/api/v1/links", json={"target_url": "https://example.com/"}, headers=token_for("eddie")
    )
    return response.json()


async def block(client, headers, link, reason="phishing"):
    return await client.post(
        f"/api/v1/links/{link['id']}/block", json={"reason": reason}, headers=headers
    )


async def test_admin_blocks_with_a_reason(client, token_for, eddies_link, metric_value):
    response = await block(client, token_for("alice"), eddies_link, "  phishing kit  ")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["blocked_reason"] == "phishing kit"
    assert body["is_active"] is True  # owner's switch untouched (D6)
    assert metric_value("shortener.links.blocked") == 1


async def test_owner_sees_the_reason_and_cannot_change_it(client, token_for, eddies_link):
    await block(client, token_for("alice"), eddies_link)
    seen = await client.get(f"/api/v1/links/{eddies_link['id']}", headers=token_for("eddie"))
    assert seen.json()["blocked_reason"] == "phishing"
    patch = await client.patch(
        f"/api/v1/links/{eddies_link['id']}", json={"is_active": True}, headers=token_for("eddie")
    )
    assert patch.status_code == 409


@pytest.mark.parametrize("reason", ["", "   ", "x" * 1001])
async def test_reason_is_required_and_bounded(client, token_for, eddies_link, reason):
    assert (await block(client, token_for("alice"), eddies_link, reason)).status_code == 422


async def test_double_block_is_409(client, token_for, eddies_link):
    await block(client, token_for("alice"), eddies_link)
    response = await block(client, token_for("alice"), eddies_link, "again")
    assert response.status_code == 409
    assert response.json()["title"] == "Link is already blocked"


@pytest.mark.parametrize(
    ("user", "status"), [("eddie", 403), ("erin", 404), ("victor", 403), ("nora", 403)]
)
async def test_only_admins_block(client, token_for, eddies_link, user, status):
    assert (await block(client, token_for(user), eddies_link)).status_code == status


async def test_unblock_restores_previous_state(client, token_for, eddies_link):
    await client.patch(
        f"/api/v1/links/{eddies_link['id']}", json={"is_active": False}, headers=token_for("eddie")
    )
    await block(client, token_for("alice"), eddies_link)
    response = await client.post(
        f"/api/v1/links/{eddies_link['id']}/unblock", headers=token_for("alice")
    )
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert response.json()["blocked_reason"] is None


async def test_unblocking_an_unblocked_link_is_409(client, token_for, eddies_link):
    response = await client.post(
        f"/api/v1/links/{eddies_link['id']}/unblock", headers=token_for("alice")
    )
    assert response.status_code == 409
    assert response.json()["title"] == "Link is not blocked"


async def test_block_unknown_link_is_404(client, token_for):
    url = "/api/v1/links/00000000-0000-0000-0000-000000000000/block"
    assert (
        await client.post(url, json={"reason": "x"}, headers=token_for("alice"))
    ).status_code == 404
