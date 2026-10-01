"""Fixtures shared by unit and integration tests. No project imports except create_app."""

import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

ISSUER = "http://localhost:8080/realms/shortener"
AUDIENCE = "shortener-api"
KID = "test-key"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

# username -> (sub, realm roles)
USERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "alice": ("sub-alice", ("admin", "default-roles-shortener")),
    "eddie": ("sub-eddie", ("editor",)),
    "erin": ("sub-erin", ("editor",)),
    "victor": ("sub-victor", ("viewer",)),
    "nora": ("sub-nora", ("default-roles-shortener",)),
}


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class ScriptedRandom:
    """RandomSource that spells out the given codes, one character per choice() call."""

    def __init__(self, codes: Iterable[str]) -> None:
        self._chars = iter("".join(codes))

    def choice(self, seq: Sequence[str]) -> str:
        char = next(self._chars)
        assert char in seq, f"{char!r} is not in the alphabet"
        return char


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def scripted_random() -> type[ScriptedRandom]:
    return ScriptedRandom


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def mint_token(signing_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _mint(
        sub: str = "sub-eddie",
        username: str = "eddie",
        roles: Iterable[str] = ("editor",),
        *,
        aud: str | list[str] = AUDIENCE,
        iss: str = ISSUER,
        expires_in: int = 300,
        kid: str = KID,
        key: Any = None,
        algorithm: str = "RS256",
        drop: Iterable[str] = (),
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": sub,
            "preferred_username": username,
            "realm_access": {"roles": list(roles)},
            "aud": aud,
            "iss": iss,
            "iat": now,
            "exp": now + expires_in,
        }
        for name in drop:
            claims.pop(name, None)
        signer = signing_key if key is None else key
        return jwt.encode(claims, signer, algorithm=algorithm, headers={"kid": kid})

    return _mint


@pytest.fixture
def token_for(mint_token: Callable[..., str]) -> Callable[[str], dict[str, str]]:
    """Authorization header for a named demo user (see USERS)."""

    def _headers(username: str) -> dict[str, str]:
        sub, roles = USERS[username]
        return {"Authorization": f"Bearer {mint_token(sub, username, roles)}"}

    return _headers


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def meter(metric_reader: InMemoryMetricReader) -> Any:
    return MeterProvider(metric_readers=[metric_reader]).get_meter("test")


@pytest.fixture
def metric_value(metric_reader: InMemoryMetricReader) -> Callable[..., float]:
    """Sum of a counter (or count of a histogram) for points matching `attributes` exactly."""

    def _value(name: str, attributes: dict[str, str] | None = None) -> float:
        data = metric_reader.get_metrics_data()
        total = 0.0
        if data is None:
            return total
        for resource in data.resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    if metric.name != name:
                        continue
                    for point in metric.data.data_points:
                        if attributes is not None and dict(point.attributes or {}) != attributes:
                            continue
                        total += getattr(point, "value", None) or getattr(point, "count", 0)
        return total

    return _value


@pytest.fixture
async def client(deps: Any) -> AsyncIterator[httpx.AsyncClient]:
    """In-process HTTP client; `deps` comes from the unit/ or integration/ conftest."""
    from shortener_api.main import create_app

    transport = httpx.ASGITransport(app=create_app(deps))
    async with httpx.AsyncClient(transport=transport, base_url="http://sho.rt") as http:
        yield http


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """build_deps() rewires the root logger (JSON handler); undo it after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
