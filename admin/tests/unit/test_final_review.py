"""Regression tests for the whole-branch review fixes."""

import base64
import json
from dataclasses import replace

import httpx
import pytest

from shortener_admin.main import create_app
from shortener_admin.oidc import OidcError

TOKEN_URL = "{INTERNAL}/protocol/openid-connect/token"
LID = "11111111-1111-1111-1111-111111111111"


def page(items=()):
    return {"items": list(items), "total": len(items), "page": 1, "page_size": 20}


# --- 1. hx-boost -------------------------------------------------------------------------


async def test_base_template_does_not_boost_navigation(client, login_as):
    await login_as()
    html = (await client.get("/")).text
    assert 'hx-boost="true"' not in html
    assert "hx-boost" not in html


async def test_boosted_navigation_gets_the_full_page(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page())
    response = await client.get(
        "/links?deleted=1", headers={"HX-Request": "true", "HX-Boosted": "true"}
    )
    assert "<nav>" in response.text and "Link deleted." in response.text
    assert 'name="q"' in response.text


async def test_unboosted_htmx_request_still_gets_the_partial(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page())
    response = await client.get("/links", headers={"HX-Request": "true"})
    assert "<nav>" not in response.text


# --- 2. return path -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("referer", "expected"),
    [
        ("http://localhost:8001/links/new", "/auth/login?next=/links/new"),
        ("http://localhost:8001/links?q=a&page=2", "/auth/login?next=/links%3Fq%3Da%26page%3D2"),
        ("http://evil.example/links", "/auth/login?next=/"),
        (None, "/auth/login?next=/"),
    ],
)
async def test_post_without_session_returns_to_the_referer_page(client, referer, expected):
    headers = {"Referer": referer} if referer else {}
    response = await client.post(
        "/links", data={"target_url": "https://x.example"}, headers=headers
    )
    assert response.status_code == 303
    assert response.headers["location"] == expected


async def test_htmx_post_without_session_prefers_hx_current_url(client):
    response = await client.post(
        "/links",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": "http://localhost:8001/links?q=z",
            "Referer": "http://localhost:8001/other",
        },
    )
    assert response.headers["hx-redirect"] == "/auth/login?next=/links%3Fq%3Dz"


async def test_api_401_keeps_the_query_string(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(401, json={"title": "Unauthorized"})
    response = await client.get("/links?q=a&page=2")
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?next=/links%3Fq%3Da%26page%3D2&expired=1"


# --- 3. transient refresh failures keep the session ---------------------------------------


async def test_refresh_connect_error_shows_503_and_keeps_the_session(
    client, mocks, ids, login_as, store
):
    session = await login_as(access_in=5)
    mocks.post(TOKEN_URL.format(**ids)).mock(side_effect=httpx.ConnectError("keycloak down"))
    response = await client.get("/links")
    assert response.status_code == 503
    assert "text/html" in response.headers["content-type"]
    assert session.id in store.sessions


async def test_refresh_5xx_from_keycloak_keeps_the_session(client, mocks, ids, login_as, store):
    session = await login_as(access_in=5)
    mocks.post(TOKEN_URL.format(**ids)).respond(502, text="bad gateway")
    assert (await client.get("/links")).status_code == 503
    assert session.id in store.sessions


async def test_refresh_me_503_keeps_the_session(client, mocks, ids, login_as, store, me_route):
    session = await login_as(access_in=5)
    mocks.post(TOKEN_URL.format(**ids)).respond(
        json={"access_token": "a2", "refresh_token": "r2", "expires_in": 300,
              "refresh_expires_in": 1800, "token_type": "Bearer"}
    )  # fmt: skip
    me_route(status=503)
    assert (await client.get("/links")).status_code == 503
    assert session.id in store.sessions


# --- 4. 503 page hides internals ----------------------------------------------------------


async def test_api_outage_page_does_not_leak_transport_details(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").mock(
        side_effect=httpx.ConnectError("All connection attempts failed to api.test:8000")
    )
    response = await client.get("/links")
    assert response.status_code == 503
    assert "API unavailable" in response.text
    assert "api.test" not in response.text and "connection attempts" not in response.text


# --- 5. ID-token algorithm pin ------------------------------------------------------------


async def test_alg_none_id_token_is_rejected_even_if_discovery_lists_it(
    client, mocks, ids, discovery, mint_id_token
):
    discovery["id_token_signing_alg_values_supported"] = ["RS256", "none"]
    mocks.get(f"{ids['INTERNAL']}/.well-known/openid-configuration").respond(json=discovery)
    login = await client.get("/auth/login?next=/links")
    state = dict(httpx.URL(login.headers["location"]).params)

    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {"iss": ids["ISSUER"], "aud": ids["CLIENT_ID"], "sub": "x",
              "nonce": state["nonce"], "exp": 4102444800, "iat": 1}  # fmt: skip
    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."
    mocks.post(TOKEN_URL.format(**ids)).respond(
        json={"access_token": "a", "refresh_token": "r", "id_token": forged,
              "expires_in": 300, "token_type": "Bearer"}
    )  # fmt: skip
    callback = await client.get(f"/auth/callback?code=c&state={state['state']}")
    assert callback.status_code == 400
    assert "sid" not in callback.cookies


# --- 6. lifespan closes resources ---------------------------------------------------------


async def test_lifespan_calls_aclose_on_shutdown(deps):
    closed: list[bool] = []

    async def spy() -> None:
        closed.append(True)

    app = create_app(replace(deps, aclose=spy))
    async with app.router.lifespan_context(app):
        assert closed == []
    assert closed == [True]


# --- 8. HTML error pages for unknown routes -----------------------------------------------


async def test_unknown_path_is_an_html_404(client):
    response = await client.get("/nope")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Page not found" in response.text


async def test_wrong_method_is_an_html_405(client, login_as):
    await login_as()
    response = await client.get(f"/links/{LID}/edit")
    assert response.status_code == 405
    assert "text/html" in response.headers["content-type"]
    assert "Not allowed" in response.text


# --- 9. no-role user POST -----------------------------------------------------------------


async def test_no_role_user_post_gets_403_and_never_reaches_the_api(client, mocks, ids, login_as):
    session = await login_as("nora", ("offline_access",))
    create = mocks.post(f"{ids['API']}/api/v1/links")
    response = await client.post(
        "/links", data={"csrf_token": session.csrf_token, "target_url": "https://x.example"}
    )
    assert response.status_code == 403
    assert "don't have access" in response.text
    assert not create.called


def test_oidc_error_has_transient_flag():
    assert OidcError("x").transient is False
    assert OidcError("x", transient=True).transient is True
