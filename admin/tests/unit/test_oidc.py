import base64
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from shortener_admin.oidc import KeycloakOidc, OidcError


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@pytest.fixture
def oidc(settings, clock):
    return KeycloakOidc(settings, clock=clock)


@pytest.fixture
def keycloak(ids, discovery, jwks_document):
    with respx.mock(assert_all_called=False) as router:
        router.route(host="admin.test").pass_through()
        router.get(f"{ids['INTERNAL']}/.well-known/openid-configuration").respond(json=discovery)
        router.get(f"{ids['INTERNAL']}/protocol/openid-connect/certs").respond(json=jwks_document)
        yield router


@pytest.fixture
async def app_client(oidc):
    async def login(request: Request):
        return await oidc.begin_login(request, request.query_params.get("next", "/"))

    async def callback(request: Request):
        try:
            tokens, next_path = await oidc.complete_login(request)
        except OidcError as exc:
            return JSONResponse(
                {"error": str(exc), "session": dict(request.session)}, status_code=400
            )
        return JSONResponse({"access": tokens.access_token, "next": next_path,
                             "access_exp": tokens.access_expires_at.isoformat(),
                             "refresh_exp": tokens.refresh_expires_at.isoformat(),
                             "session": dict(request.session)})  # fmt: skip

    app = Starlette(
        routes=[Route("/auth/login", login), Route("/auth/callback", callback)],
        middleware=[Middleware(SessionMiddleware, secret_key="c" * 32, session_cookie="login_state",
                               path="/auth", max_age=600, same_site="lax", https_only=False)],
    )  # fmt: skip
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://admin.test") as client:
        yield client


async def start_login(app_client, next_path="/links"):
    response = await app_client.get("/auth/login", params={"next": next_path})
    assert response.status_code == 302
    return query(response.headers["location"]), response


async def test_login_redirects_to_keycloak_with_pkce(keycloak, app_client, ids):
    params, response = await start_login(app_client)
    assert response.headers["location"].startswith(f"{ids['ISSUER']}/protocol/openid-connect/auth?")
    assert params["response_type"] == "code"
    assert params["client_id"] == "shortener-admin"
    assert params["redirect_uri"] == "http://localhost:8001/auth/callback"
    assert params["scope"] == "openid"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 20 and len(params["nonce"]) >= 20
    cookie = response.headers["set-cookie"]
    assert "login_state=" in cookie and "path=/auth" in cookie.lower()
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()


async def test_callback_exchanges_code_and_returns_tokens(
    keycloak, app_client, ids, token_body, clock
):
    params, _ = await start_login(app_client, "/links?page=2")
    token_route = keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"])
    )
    response = await app_client.get(
        "/auth/callback", params={"code": "the-code", "state": params["state"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access"] == "access-1"
    assert body["next"] == "/links?page=2"
    assert body["access_exp"] == (clock() + timedelta(seconds=300)).isoformat()
    assert body["refresh_exp"] == (clock() + timedelta(seconds=1800)).isoformat()
    assert body["session"] == {}  # handshake data cleared
    request = token_route.calls.last.request
    form = query("?" + request.content.decode())
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code"
    assert form["redirect_uri"] == "http://localhost:8001/auth/callback"
    assert len(form["code_verifier"]) >= 43
    assert (
        request.headers["authorization"]
        == "Basic " + base64.b64encode(b"shortener-admin:s3cret").decode()
    )


async def test_offsite_next_is_sanitized(keycloak, app_client, ids, token_body):
    params, _ = await start_login(app_client, "//evil.example/x")
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"])
    )
    response = await app_client.get(
        "/auth/callback", params={"code": "c", "state": params["state"]}
    )
    assert response.json()["next"] == "/"


async def test_callback_without_handshake_cookie_fails_without_calling_keycloak(
    keycloak, app_client, ids
):
    token_route = keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token")
    response = await app_client.get("/auth/callback", params={"code": "c", "state": "s"})
    assert response.status_code == 400
    assert not token_route.called


async def test_state_mismatch_fails_and_clears_handshake(keycloak, app_client, ids):
    await start_login(app_client)
    token_route = keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token")
    response = await app_client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert response.status_code == 400
    assert response.json()["session"] == {}
    assert not token_route.called


async def test_keycloak_error_parameter_fails(keycloak, app_client):
    params, _ = await start_login(app_client)
    response = await app_client.get(
        "/auth/callback", params={"error": "access_denied", "state": params["state"]}
    )
    assert response.status_code == 400


async def test_rejected_code_fails(keycloak, app_client, ids):
    params, _ = await start_login(app_client)
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        400, json={"error": "invalid_grant", "error_description": "Code not valid"}
    )
    response = await app_client.get(
        "/auth/callback", params={"code": "replayed", "state": params["state"]}
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "bad",
    [{"nonce": "other"}, {"iss": "http://keycloak:8080/realms/shortener"}, {"aud": "shortener-api"},
     {"exp": 1000}],
    ids=["nonce", "issuer", "audience", "expired"],
)  # fmt: skip
async def test_bad_id_token_fails(keycloak, app_client, ids, token_body, mint_id_token, bad):
    params, _ = await start_login(app_client)
    claims = dict(bad)
    nonce = claims.pop("nonce", params["nonce"])
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"], id_token=mint_id_token(nonce, **claims))
    )
    response = await app_client.get(
        "/auth/callback", params={"code": "c", "state": params["state"]}
    )
    assert response.status_code == 400


async def test_id_token_signed_by_another_key_fails(
    keycloak, app_client, ids, token_body, mint_id_token
):
    params, _ = await start_login(app_client)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"], id_token=mint_id_token(params["nonce"], key=other))
    )
    response = await app_client.get(
        "/auth/callback", params={"code": "c", "state": params["state"]}
    )
    assert response.status_code == 400


async def test_hs256_id_token_fails_even_though_keycloak_advertises_hs256(
    keycloak, app_client, ids, token_body, mint_id_token
):
    params, _ = await start_login(app_client)
    forged = mint_id_token(
        params["nonce"], key="guessable-secret-guessable-secret!", algorithm="HS256"
    )
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"], id_token=forged)
    )
    response = await app_client.get(
        "/auth/callback", params={"code": "c", "state": params["state"]}
    )
    assert response.status_code == 400


async def test_refresh_returns_new_tokens_and_keeps_old_id_token(keycloak, oidc, ids):
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 300,
              "refresh_expires_in": 1800, "token_type": "Bearer"}
    )  # fmt: skip
    tokens = await oidc.refresh("refresh-1", "old-id-token")
    assert (tokens.access_token, tokens.refresh_token, tokens.id_token) == (
        "access-2",
        "refresh-2",
        "old-id-token",
    )


async def test_refresh_rejected_is_oidc_error(keycloak, oidc, ids):
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        400, json={"error": "invalid_grant"}
    )
    with pytest.raises(OidcError):
        await oidc.refresh("revoked", "id")


async def test_keycloak_unreachable_is_oidc_error(keycloak, oidc, ids):
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").mock(
        side_effect=httpx.ConnectError("down")
    )
    with pytest.raises(OidcError):
        await oidc.refresh("r", "id")


async def test_end_session_url_comes_from_discovery(keycloak, oidc, ids):
    url = await oidc.end_session_url("the-id-token", "http://localhost:8001/")
    assert url.startswith(f"{ids['ISSUER']}/protocol/openid-connect/logout?")
    assert query(url) == {"id_token_hint": "the-id-token", "post_logout_redirect_uri": "http://localhost:8001/",
                          "client_id": "shortener-admin"}  # fmt: skip
