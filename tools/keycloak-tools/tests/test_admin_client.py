import json

import httpx
import respx

from keycloak_tools.admin_client import KeycloakAdminClient
from keycloak_tools.users import DesiredUser

BASE = "http://kc.test"
USERS = f"{BASE}/admin/realms/shortener/users"
NORA = DesiredUser(username="nora", email="n@x.test", first_name="N", last_name="R", password="pw")


def make_client() -> KeycloakAdminClient:
    respx.post(f"{BASE}/realms/master/protocol/openid-connect/token").respond(
        json={"access_token": "admin-token"}
    )
    return KeycloakAdminClient(BASE, "shortener", "kcadmin", "pw", httpx.Client(base_url=BASE))


@respx.mock
def test_list_users_includes_realm_roles_and_sends_bearer_token():
    client = make_client()
    users_route = respx.get(USERS).respond(
        json=[
            {
                "id": "u1",
                "username": "alice",
                "email": "a@x.test",
                "firstName": "A",
                "lastName": "B",
                "enabled": True,
            }
        ]
    )
    respx.get(f"{USERS}/u1/role-mappings/realm").respond(
        json=[{"name": "admin"}, {"name": "default-roles-shortener"}]
    )
    [alice] = client.list_users()
    assert alice.roles == frozenset({"admin", "default-roles-shortener"})
    assert users_route.calls.last.request.headers["Authorization"] == "Bearer admin-token"


@respx.mock
def test_create_user_returns_id_from_location_header():
    client = make_client()
    route = respx.post(USERS).respond(201, headers={"Location": f"{USERS}/new-id"})
    assert client.create_user(NORA) == "new-id"
    body = json.loads(route.calls.last.request.content)
    assert body["credentials"] == [{"type": "password", "value": "pw", "temporary": False}]
    assert body["emailVerified"] is True


@respx.mock
def test_profile_update_and_password_reset_payloads():
    client = make_client()
    put_user = respx.put(f"{USERS}/u1").respond(204)
    put_password = respx.put(f"{USERS}/u1/reset-password").respond(204)
    client.update_profile("u1", NORA)
    client.set_password("u1", "new-pw")
    profile = json.loads(put_user.calls.last.request.content)
    assert profile["enabled"] is True
    assert profile["email"] == "n@x.test"
    assert json.loads(put_password.calls.last.request.content) == {
        "type": "password",
        "value": "new-pw",
        "temporary": False,
    }


@respx.mock
def test_role_changes_send_full_role_representations():
    client = make_client()
    respx.get(f"{BASE}/admin/realms/shortener/roles/editor").respond(
        json={"id": "r1", "name": "editor"}
    )
    add = respx.post(f"{USERS}/u1/role-mappings/realm").respond(204)
    remove = respx.delete(f"{USERS}/u1/role-mappings/realm").respond(204)
    client.add_realm_roles("u1", frozenset({"editor"}))
    client.remove_realm_roles("u1", frozenset({"editor"}))
    assert json.loads(add.calls.last.request.content) == [{"id": "r1", "name": "editor"}]
    assert json.loads(remove.calls.last.request.content) == [{"id": "r1", "name": "editor"}]
