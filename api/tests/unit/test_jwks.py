import json
import random
from datetime import timedelta

import httpx
import jwt
import pytest
import respx

from shortener_api.auth import (
    HttpJwksProvider,
    JwksUnavailableError,
    TokenValidator,
    UnknownKeyError,
)
from shortener_api.deps import AppDeps
from shortener_api.main import create_app

URL = "http://keycloak.test/realms/shortener/protocol/openid-connect/certs"


def jwks(signing_key, kid="test-key") -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    return {"keys": [jwk | {"kid": kid, "use": "sig", "alg": "RS256"}]}


@pytest.fixture
def provider(clock):
    return HttpJwksProvider(URL, httpx.AsyncClient(), clock)


@respx.mock
async def test_fetches_once_and_caches(provider, signing_key):
    route = respx.get(URL).respond(json=jwks(signing_key))
    await provider.get_key("test-key")
    await provider.get_key("test-key")
    assert route.call_count == 1


@respx.mock
async def test_unknown_kid_refreshes_only_after_the_interval(provider, signing_key, clock):
    route = respx.get(URL).respond(json=jwks(signing_key))
    await provider.get_key("test-key")
    with pytest.raises(UnknownKeyError):
        await provider.get_key("rotated")
    assert route.call_count == 1  # within the refresh interval: no refetch
    clock.advance(timedelta(seconds=31))
    route.respond(json=jwks(signing_key, kid="rotated"))
    assert await provider.get_key("rotated") is not None
    assert route.call_count == 2


@respx.mock
async def test_cold_start_failure_is_unavailable(provider):
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(JwksUnavailableError):
        await provider.get_key("test-key")


@respx.mock
async def test_cached_keys_survive_a_later_outage(provider, signing_key, clock):
    route = respx.get(URL).respond(json=jwks(signing_key))
    await provider.get_key("test-key")
    clock.advance(timedelta(minutes=5))
    route.mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(UnknownKeyError):
        await provider.get_key("rotated")
    assert await provider.get_key("test-key") is not None


@respx.mock
async def test_malformed_jwks_on_cold_start_is_unavailable(provider):
    respx.get(URL).respond(json={"nope": True})
    with pytest.raises(JwksUnavailableError):
        await provider.get_key("test-key")


async def test_me_is_503_when_keys_cannot_be_fetched(settings, dead_engine, clock, mint_token):
    unreachable = HttpJwksProvider("http://127.0.0.1:1/certs", httpx.AsyncClient(), clock)
    deps = AppDeps(
        settings=settings,
        engine=dead_engine,
        clock=clock,
        rng=random.Random(1),
        token_validator=TokenValidator(unreachable, settings.oidc_issuer, "shortener-api"),
    )
    transport = httpx.ASGITransport(app=create_app(deps))
    async with httpx.AsyncClient(transport=transport, base_url="http://sho.rt") as http:
        response = await http.get("/api/v1/me", headers={"Authorization": f"Bearer {mint_token()}"})
    assert response.status_code == 503
    assert response.json()["detail"] == "identity provider unavailable"
