from urllib.parse import parse_qs, urlsplit


def q(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def login_round_trip(client, mocks, ids, token_body, next_path="/links"):
    started = await client.get("/auth/login", params={"next": next_path})
    assert started.status_code == 302
    params = q(started.headers["location"])
    mocks.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"])
    )
    return await client.get("/auth/callback", params={"code": "c", "state": params["state"]})


async def test_login_redirects_to_keycloak(client, mocks, ids):
    response = await client.get("/auth/login", params={"next": "/links"})
    assert response.status_code == 302
    assert response.headers["location"].startswith(f"{ids['ISSUER']}/protocol/openid-connect/auth?")


async def test_callback_creates_session_and_redirects_to_next(
    client, mocks, ids, token_body, store, me_route
):
    me_route("alice", ("admin",))
    response = await login_round_trip(client, mocks, ids, token_body, "/links?page=2")
    assert response.status_code == 303
    assert response.headers["location"] == "/links?page=2"
    cookie = response.headers["set-cookie"].lower()
    assert (
        "sid=" in cookie
        and "httponly" in cookie
        and "samesite=lax" in cookie
        and "path=/" in cookie
    )
    [session] = store.sessions.values()
    assert (session.username, session.roles) == ("alice", frozenset({"admin"}))
    assert session.tokens.access_token == "access-1"


async def test_callback_ignores_an_offsite_next(client, mocks, ids, token_body, me_route):
    me_route()
    response = await login_round_trip(client, mocks, ids, token_body, "https://evil.example/")
    assert response.headers["location"] == "/"


async def test_failed_callback_renders_400_and_creates_no_session(client, mocks, store):
    response = await client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert response.status_code == 400
    assert "Sign-in failed" in response.text
    assert store.sessions == {}


async def test_callback_fails_when_api_rejects_the_new_token(
    client, mocks, ids, token_body, store, me_route
):
    me_route(status=401)
    response = await login_round_trip(client, mocks, ids, token_body)
    assert response.status_code == 400
    assert store.sessions == {}


async def test_new_login_replaces_an_existing_session(
    client, mocks, ids, token_body, store, me_route, login_as
):
    old = await login_as()
    me_route()
    await login_round_trip(client, mocks, ids, token_body)
    assert old.id not in store.sessions
    assert len(store.sessions) == 1


async def test_session_expired_page(client):
    response = await client.get("/auth/login", params={"expired": "1", "next": "/links"})
    assert response.status_code == 200
    assert "session expired" in response.text.lower()
    assert 'href="/auth/login?next=/links"' in response.text


async def test_logout_requires_csrf(client, mocks, login_as, store):
    session = await login_as()
    response = await client.post("/auth/logout")
    assert response.status_code == 403
    assert session.id in store.sessions


async def test_logout_ends_session_and_redirects_to_keycloak(client, mocks, ids, login_as, store):
    session = await login_as()
    response = await client.post("/auth/logout", data={"csrf_token": session.csrf_token})
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"{ids['ISSUER']}/protocol/openid-connect/logout?")
    assert q(location)["id_token_hint"] == "id-eddie"
    assert session.id not in store.sessions
    assert (
        'sid=""' in response.headers["set-cookie"]
        or "max-age=0" in response.headers["set-cookie"].lower()
    )
