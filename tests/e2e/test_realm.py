import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def admin_http(e2e_settings):
    token = (
        httpx.post(
            f"{e2e_settings.keycloak_url}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": e2e_settings.keycloak_admin_user,
                "password": e2e_settings.keycloak_admin_password,
            },
            timeout=10,
        )
        .raise_for_status()
        .json()["access_token"]
    )
    base = f"{e2e_settings.keycloak_url}/admin/realms/{e2e_settings.keycloak_realm}"
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=10) as c:
        yield c


def clients_by_id(admin_http) -> dict[str, dict]:
    return {c["clientId"]: c for c in admin_http.get("/clients").raise_for_status().json()}


def test_issuer_is_the_browser_facing_url(e2e_settings):
    url = f"{e2e_settings.keycloak_url}/realms/shortener/.well-known/openid-configuration"
    discovery = httpx.get(url, timeout=10).raise_for_status().json()
    assert discovery["issuer"] == f"{e2e_settings.keycloak_url}/realms/shortener"


def test_realm_roles_exist(admin_http):
    names = {r["name"] for r in admin_http.get("/roles").raise_for_status().json()}
    assert {"admin", "editor", "viewer"} <= names


def test_admin_client_is_confidential_with_pkce(admin_http):
    client = clients_by_id(admin_http)["shortener-admin"]
    assert client["publicClient"] is False
    assert client["standardFlowEnabled"] is True
    assert client["directAccessGrantsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert "http://localhost:8001/auth/callback" in client["redirectUris"]


def test_admin_client_secret_comes_from_env(admin_http, e2e_settings):
    client = clients_by_id(admin_http)["shortener-admin"]
    secret = admin_http.get(f"/clients/{client['id']}/client-secret").raise_for_status().json()
    assert secret["value"] == e2e_settings.shortener_admin_client_secret


def test_api_client_has_no_flows(admin_http):
    client = clients_by_id(admin_http)["shortener-api"]
    assert not client["standardFlowEnabled"]
    assert not client["directAccessGrantsEnabled"]
    assert not client["serviceAccountsEnabled"]


def test_dev_client_is_public_with_password_grant(admin_http):
    client = clients_by_id(admin_http)["shortener-dev"]
    assert client["publicClient"] is True
    assert client["directAccessGrantsEnabled"] is True
    assert client["standardFlowEnabled"] is False


@pytest.mark.parametrize("client_id", ["shortener-admin", "shortener-dev"])
def test_audience_mapper_adds_shortener_api(admin_http, client_id):
    mappers = clients_by_id(admin_http)[client_id].get("protocolMappers", [])
    audiences = {
        m["config"].get("included.client.audience")
        for m in mappers
        if m["protocolMapper"] == "oidc-audience-mapper"
    }
    assert "shortener-api" in audiences
