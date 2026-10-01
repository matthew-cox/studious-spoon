import base64
import json
from pathlib import Path

import httpx
import pytest

from keycloak_tools.admin_client import KeycloakAdminClient
from keycloak_tools.seed import run_seed
from keycloak_tools.users import load_users

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_ROLES = {
    "alice": {"admin"},
    "eddie": {"editor"},
    "erin": {"editor"},
    "victor": {"viewer"},
    "nora": set(),
}


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.mark.parametrize("username", sorted(EXPECTED_ROLES))
def test_seeded_user_gets_token_with_roles_audience_and_sub(e2e_settings, username):
    response = httpx.post(
        f"{e2e_settings.keycloak_url}/realms/shortener/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "shortener-dev",
            "username": username,
            "password": "password",
            "scope": "openid",
        },
        timeout=10,
    )
    assert response.status_code == 200, response.text
    token_claims = claims(response.json()["access_token"])
    assert token_claims["iss"] == f"{e2e_settings.keycloak_url}/realms/shortener"
    assert token_claims["sub"]
    assert token_claims["preferred_username"] == username
    aud = token_claims["aud"]
    assert "shortener-api" in ([aud] if isinstance(aud, str) else aud)
    roles = set(token_claims.get("realm_access", {}).get("roles", []))
    assert roles & {"admin", "editor", "viewer"} == EXPECTED_ROLES[username]


def test_reseed_is_noop(e2e_settings):
    admin = KeycloakAdminClient(
        e2e_settings.keycloak_url,
        "shortener",
        e2e_settings.keycloak_admin_user,
        e2e_settings.keycloak_admin_password,
    )
    assert run_seed(admin, load_users(REPO_ROOT / "infra/keycloak/users.yaml")) == []
