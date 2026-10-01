from datetime import timedelta
from typing import Annotated, Any

import pytest
from fastapi import Depends

from shortener_admin.auth import require_access, require_session
from shortener_admin.deps import get_deps


@pytest.fixture
def probe(app):
    @app.get("/__probe")
    async def _probe(session: Annotated[Any, Depends(require_session)]):
        return {"user": session.username, "access": session.tokens.access_token}

    @app.get("/__probe_access")
    async def _probe_access(session: Annotated[Any, Depends(require_access)]):
        return {"user": session.username}

    return app


async def test_anonymous_page_request_redirects_to_login(probe, client):
    response = await client.get("/__probe?x=1")
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?next=/__probe%3Fx%3D1"


async def test_anonymous_htmx_request_gets_hx_redirect_not_a_login_page(probe, client):
    response = await client.get("/__probe", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert response.headers["hx-redirect"].startswith("/auth/login?next=")
    assert response.text == ""


async def test_fresh_session_is_used_as_is(probe, client, login_as):
    await login_as()
    assert (await client.get("/__probe")).json() == {"user": "eddie", "access": "access-eddie"}


async def test_expiring_access_token_is_refreshed_and_roles_reread(
    probe, client, mocks, ids, login_as, store, me_route
):
    session = await login_as(access_in=20)  # within the 30 s margin
    mocks.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 300,
              "refresh_expires_in": 1800, "token_type": "Bearer"}
    )  # fmt: skip
    me_route("eddie", ("viewer",))
    assert (await client.get("/__probe")).json()["access"] == "access-2"
    assert store.sessions[session.id].roles == frozenset({"viewer"})
    assert store.sessions[session.id].tokens.id_token == "id-eddie"  # kept: refresh omitted it


@pytest.mark.parametrize("htmx", [False, True])
async def test_failed_refresh_deletes_session_and_sends_to_expired_login(
    probe, client, mocks, ids, login_as, store, htmx
):
    session = await login_as(access_in=5)
    mocks.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        400, json={"error": "invalid_grant"}
    )
    response = await client.get("/__probe", headers={"HX-Request": "true"} if htmx else {})
    assert session.id not in store.sessions
    location = response.headers["hx-redirect" if htmx else "location"]
    assert "expired=1" in location


async def test_session_past_refresh_expiry_requires_login(probe, client, login_as, clock):
    await login_as(refresh_in=60)
    clock.advance(timedelta(seconds=61))
    assert (await client.get("/__probe")).status_code == 303


async def test_user_without_roles_gets_no_access_page(probe, client, login_as):
    await login_as("nora", ("offline_access",))
    response = await client.get("/__probe_access")
    assert response.status_code == 403
    assert "don't have access" in response.text
    assert "Sign out" in response.text


async def test_api_401_mid_request_ends_the_session(app, client, mocks, ids, login_as, store):
    @app.get("/__probe_api")
    async def _probe_api(
        session: Annotated[Any, Depends(require_access)], deps: Annotated[Any, Depends(get_deps)]
    ):
        return await deps.api.summary(session.tokens.access_token)

    session = await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(
        401, json={"title": "Unauthorized", "status": 401}
    )
    response = await client.get("/__probe_api")
    assert response.status_code == 303
    assert "expired=1" in response.headers["location"]
    assert session.id not in store.sessions
