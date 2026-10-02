from datetime import timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def eddies_link(client, token_for):
    response = await client.post(
        "/api/v1/links", json={"target_url": "https://example.com/"}, headers=token_for("eddie")
    )
    return response.json()


async def history(client, headers, link_id):
    return await client.get(f"/api/v1/links/{link_id}/events", headers=headers)


async def test_block_and_unblock_are_recorded_with_actor_and_reason(
    client, token_for, eddies_link, clock
):
    alice = token_for("alice")
    await client.post(
        f"/api/v1/links/{eddies_link['id']}/block", json={"reason": " phishing "}, headers=alice
    )
    clock.advance(timedelta(minutes=5))
    await client.post(f"/api/v1/links/{eddies_link['id']}/unblock", headers=alice)

    response = await history(client, alice, eddies_link["id"])

    assert response.status_code == 200
    events = response.json()
    assert [e["action"] for e in events] == ["block", "unblock"]  # oldest first
    assert events[0]["actor_username"] == "alice"
    assert events[0]["reason"] == "phishing"
    assert events[0]["link_code"] == eddies_link["code"]
    assert events[1]["reason"] is None
    assert events[1]["occurred_at"] > events[0]["occurred_at"]


async def test_history_survives_deleting_the_link(client, token_for, eddies_link):
    alice = token_for("alice")
    await client.post(
        f"/api/v1/links/{eddies_link['id']}/block", json={"reason": "phishing"}, headers=alice
    )
    assert (
        await client.delete(f"/api/v1/links/{eddies_link['id']}", headers=alice)
    ).status_code == 204

    events = (await history(client, alice, eddies_link["id"])).json()

    assert [(e["action"], e["actor_username"]) for e in events] == [
        ("block", "alice"),
        ("delete", "alice"),
    ]


async def test_owner_delete_is_recorded(client, token_for, eddies_link):
    await client.delete(f"/api/v1/links/{eddies_link['id']}", headers=token_for("eddie"))
    events = (await history(client, token_for("alice"), eddies_link["id"])).json()
    assert [(e["action"], e["actor_username"]) for e in events] == [("delete", "eddie")]


async def test_rejected_actions_leave_no_record(client, token_for, eddies_link):
    alice = token_for("alice")
    path = f"/api/v1/links/{eddies_link['id']}"
    await client.post(f"{path}/block", json={"reason": "phishing"}, headers=alice)
    assert (
        await client.post(f"{path}/block", json={"reason": "x"}, headers=alice)
    ).status_code == 409
    assert (await client.delete(path, headers=token_for("eddie"))).status_code == 409
    assert (
        await client.post(f"{path}/block", json={"reason": "x"}, headers=token_for("erin"))
    ).status_code == 404

    events = (await history(client, alice, eddies_link["id"])).json()

    assert [e["action"] for e in events] == ["block"]


@pytest.mark.parametrize(
    ("user", "status"), [("eddie", 403), ("erin", 404), ("victor", 403), ("nora", 403)]
)
async def test_only_admins_see_history(client, token_for, eddies_link, user, status):
    assert (await history(client, token_for(user), eddies_link["id"])).status_code == status


async def test_non_admin_gets_404_once_the_link_is_deleted(client, token_for, eddies_link):
    await client.delete(f"/api/v1/links/{eddies_link['id']}", headers=token_for("eddie"))
    assert (await history(client, token_for("eddie"), eddies_link["id"])).status_code == 404


async def test_unknown_link_is_404_for_admins(client, token_for):
    assert (await history(client, token_for("alice"), uuid4())).status_code == 404


async def test_owner_view_of_the_link_does_not_name_the_admin(client, token_for, eddies_link):
    await client.post(
        f"/api/v1/links/{eddies_link['id']}/block",
        json={"reason": "phishing"},
        headers=token_for("alice"),
    )
    seen = (
        await client.get(f"/api/v1/links/{eddies_link['id']}", headers=token_for("eddie"))
    ).json()
    assert "alice" not in seen.values()
    assert "blocked_by" not in seen
