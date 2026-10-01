from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx

from shortener_admin.api_client import ApiClient
from shortener_admin.deps import AdminDeps
from shortener_admin.main import create_app, make_templates
from shortener_admin.oidc import KeycloakOidc
from shortener_admin.sessions import InMemorySessionStore, Session, TokenSet


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def mocks(ids, discovery, jwks_document):
    """One respx router for both outside services: Keycloak (internal URL) and the API."""
    with respx.mock(assert_all_called=False) as router:
        router.route(host="localhost").pass_through()  # the app under test (ASGI)
        router.get(f"{ids['INTERNAL']}/.well-known/openid-configuration").respond(json=discovery)
        router.get(f"{ids['INTERNAL']}/protocol/openid-connect/certs").respond(json=jwks_document)
        yield router


@pytest.fixture
async def deps(settings, clock, store, ids) -> AsyncIterator[AdminDeps]:
    http = httpx.AsyncClient(base_url=ids["API"])
    yield AdminDeps(
        settings=settings,
        sessions=store,
        oidc=KeycloakOidc(settings, clock=clock),
        api=ApiClient(http),
        clock=clock,
        templates=make_templates(),
    )
    await http.aclose()


@pytest.fixture
async def app(deps):
    return create_app(deps)


@pytest.fixture
async def client(app, ids) -> AsyncIterator[httpx.AsyncClient]:
    # ServerErrorMiddleware re-raises after rendering the 500 page; let tests see the response.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=ids["PUBLIC"]) as http:
        yield http


@pytest.fixture
def login_as(store, client, clock) -> Callable[..., Awaitable[Session]]:
    async def _login(username: str = "eddie", roles: tuple[str, ...] = ("editor",), *,
                     access_in: int = 300, refresh_in: int = 1800) -> Session:  # fmt: skip
        tokens = TokenSet(
            access_token=f"access-{username}",
            refresh_token=f"refresh-{username}",
            id_token=f"id-{username}",
            access_expires_at=clock() + timedelta(seconds=access_in),
            refresh_expires_at=clock() + timedelta(seconds=refresh_in),
        )
        session = await store.create(
            sub=f"sub-{username}",
            username=username,
            roles=frozenset(roles),
            tokens=tokens,
            now=clock(),
        )
        client.cookies.set("sid", session.id)
        return session

    return _login


@pytest.fixture
def me_route(mocks, ids) -> Callable[..., Any]:
    def _route(
        username: str = "eddie", roles: tuple[str, ...] = ("editor",), status: int = 200
    ) -> Any:
        body = {"sub": f"sub-{username}", "username": username, "roles": list(roles)}
        return mocks.get(f"{ids['API']}/api/v1/me").respond(status, json=body)

    return _route
