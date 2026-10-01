"""Shared admin test fixtures: RSA key, ID-token minting, discovery, URLs, a fixed clock."""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from shortener_admin.settings import AdminSettings

INTERNAL = "http://keycloak.test/realms/shortener"  # token, certs, discovery (container network)
ISSUER = "http://localhost:8080/realms/shortener"  # auth, logout, `iss` (browser-facing)
API = "http://api.test"
PUBLIC = "http://localhost:8001"
CLIENT_ID = "shortener-admin"
KID = "kc-key"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> dict[str, str]:
    """URL constants for tests (test modules can't import each other)."""
    return {
        "INTERNAL": INTERNAL,
        "ISSUER": ISSUER,
        "API": API,
        "PUBLIC": PUBLIC,
        "CLIENT_ID": CLIENT_ID,
    }


@pytest.fixture
def settings() -> AdminSettings:
    return AdminSettings(
        database_url="postgresql+psycopg://admin_user:x@127.0.0.1:1/shortener",
        api_base_url=API,
        public_base_url=PUBLIC,
        oidc_internal_url=INTERNAL,
        oidc_client_secret="s3cret",
        cookie_secret="c" * 32,
    )


@pytest.fixture
def discovery() -> dict[str, Any]:
    """Shape of Keycloak's real discovery document as fetched over the internal hostname."""
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
        "token_endpoint": f"{INTERNAL}/protocol/openid-connect/token",
        "jwks_uri": f"{INTERNAL}/protocol/openid-connect/certs",
        "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
        # Keycloak advertises many algorithms, HS256 included; HS256 must still be rejected.
        "id_token_signing_alg_values_supported": ["RS256", "HS256", "ES256"],
    }


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwks_document(signing_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    return {"keys": [jwk | {"kid": KID, "use": "sig", "alg": "RS256"}]}


@pytest.fixture
def mint_id_token(signing_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _mint(nonce: str, *, key: Any = None, algorithm: str = "RS256", **claims: Any) -> str:
        now = int(time.time())
        body = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "sub-eddie", "nonce": nonce,
                "iat": now, "exp": now + 300, "azp": CLIENT_ID} | claims  # fmt: skip
        signer = key if key is not None else signing_key
        return jwt.encode(body, signer, algorithm=algorithm, headers={"kid": KID})

    return _mint


@pytest.fixture
def token_body(mint_id_token: Callable[..., str]) -> Callable[..., dict[str, Any]]:
    def _body(nonce: str, suffix: str = "1", **overrides: Any) -> dict[str, Any]:
        return {
            "access_token": f"access-{suffix}",
            "refresh_token": f"refresh-{suffix}",
            "id_token": mint_id_token(nonce),
            "expires_in": 300,
            "refresh_expires_in": 1800,
            "token_type": "Bearer",
        } | overrides

    return _body
