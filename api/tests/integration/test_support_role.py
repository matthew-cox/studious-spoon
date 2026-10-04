"""The support role moderates any link but never creates, edits or deletes one."""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def eddies_link(client, token_for):
    response = await client.post(
        "/api/v1/links", json={"target_url": "https://example.com/"}, headers=token_for("eddie")
    )
    return response.json()


async def test_support_sees_every_link_and_its_stats(client, token_for, eddies_link):
    sam = token_for("sam")
    listed = (await client.get("/api/v1/links", headers=sam)).json()
    assert [i["id"] for i in listed["items"]] == [eddies_link["id"]]
    path = f"/api/v1/links/{eddies_link['id']}"
    assert (await client.get(path, headers=sam)).status_code == 200
    assert (await client.get(f"{path}/stats", headers=sam)).status_code == 200


async def test_support_blocks_unblocks_and_reads_history(client, token_for, eddies_link):
    sam = token_for("sam")
    path = f"/api/v1/links/{eddies_link['id']}"
    blocked = await client.post(f"{path}/block", json={"reason": "phishing"}, headers=sam)
    assert blocked.status_code == 200 and blocked.json()["status"] == "blocked"
    assert (
        await client.post(f"{path}/unblock", json={"reason": "false report"}, headers=sam)
    ).status_code == 200

    events = (await client.get(f"{path}/events", headers=sam)).json()

    assert [(e["action"], e["actor_username"]) for e in events] == [
        ("block", "sam"),
        ("unblock", "sam"),
    ]


async def test_support_cannot_create_edit_or_delete(client, token_for, eddies_link):
    sam = token_for("sam")
    path = f"/api/v1/links/{eddies_link['id']}"
    created = await client.post(
        "/api/v1/links", json={"target_url": "https://example.com/"}, headers=sam
    )
    assert created.status_code == 403
    assert (await client.patch(path, json={"is_active": False}, headers=sam)).status_code == 403
    assert (await client.delete(path, headers=sam)).status_code == 403
    await client.post(f"{path}/block", json={"reason": "phishing"}, headers=sam)
    assert (await client.delete(path, headers=sam)).status_code == 403  # blocked or not


async def test_support_reads_history_of_a_deleted_link(client, token_for, eddies_link):
    path = f"/api/v1/links/{eddies_link['id']}"
    await client.post(f"{path}/block", json={"reason": "phishing"}, headers=token_for("sam"))
    await client.delete(path, headers=token_for("alice"))

    events = (await client.get(f"{path}/events", headers=token_for("sam"))).json()

    assert [e["action"] for e in events] == ["block", "delete"]
