"""Bearer-token validation against Keycloak's JWKS (spec §7.2, §7.3)."""

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any, Protocol

import httpx
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shortener_api.clock import Clock
from shortener_api.deps import AppDeps, get_deps
from shortener_api.errors import ProblemError
from shortener_api.policy import Principal


class UnknownKeyError(Exception):
    """No signing key with this kid (after any permitted refresh)."""


class JwksUnavailableError(Exception):
    """Keys could never be fetched (cold start with Keycloak unreachable)."""


class InvalidTokenError(Exception):
    pass


class JwksProvider(Protocol):
    async def get_key(self, kid: str) -> Any: ...


class StaticJwksProvider:
    def __init__(self, keys: Mapping[str, Any]) -> None:
        self._keys = dict(keys)

    async def get_key(self, kid: str) -> Any:
        try:
            return self._keys[kid]
        except KeyError as exc:
            raise UnknownKeyError(kid) from exc


class HttpJwksProvider:
    """Caches Keycloak's keys; refetches on an unknown kid at most once per interval."""

    def __init__(
        self,
        jwks_url: str,
        http: httpx.AsyncClient,
        clock: Clock,
        min_refresh_interval: timedelta = timedelta(seconds=30),
    ) -> None:
        self._url = jwks_url
        self._http = http
        self._clock = clock
        self._min_refresh = min_refresh_interval
        self._keys: dict[str, Any] = {}
        self._fetched_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> Any:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            if kid not in self._keys and self._may_refresh():
                await self._refresh()
        if kid in self._keys:
            return self._keys[kid]
        raise UnknownKeyError(kid)

    def _may_refresh(self) -> bool:
        return self._fetched_at is None or self._clock.now() - self._fetched_at >= self._min_refresh

    async def _refresh(self) -> None:
        self._fetched_at = self._clock.now()
        try:
            response = await self._http.get(self._url)
            response.raise_for_status()
            jwk_set = jwt.PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, jwt.PyJWTError, ValueError) as exc:
            if not self._keys:
                raise JwksUnavailableError(str(exc)) from exc
            return  # keep serving cached keys (spec §9)
        self._keys = {key.key_id: key.key for key in jwk_set.keys if key.key_id}


class TokenValidator:
    def __init__(
        self, jwks: JwksProvider, issuer: str, audience: str, leeway_seconds: int = 30
    ) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds

    async def principal(self, token: str) -> Principal:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("malformed token") from exc
        if not isinstance(kid, str):
            raise InvalidTokenError("token has no kid")
        try:
            key = await self._jwks.get_key(kid)
        except UnknownKeyError as exc:
            raise InvalidTokenError("unknown signing key") from exc
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(str(exc)) from exc
        realm_access = claims.get("realm_access")
        raw_roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
        roles = frozenset(role for role in raw_roles if isinstance(role, str))
        sub = str(claims["sub"])
        return Principal(
            sub=sub, username=str(claims.get("preferred_username") or sub), roles=roles
        )


_bearer = HTTPBearer(auto_error=False)
_CHALLENGE = 'Bearer realm="shortener"'


async def current_principal(
    deps: Annotated[AppDeps, Depends(get_deps)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ProblemError(
            401, detail="missing bearer token", headers={"WWW-Authenticate": _CHALLENGE}
        )
    try:
        return await deps.token_validator.principal(credentials.credentials)
    except InvalidTokenError as exc:
        raise ProblemError(
            401,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": f'{_CHALLENGE}, error="invalid_token"'},
        ) from exc
    except JwksUnavailableError as exc:
        raise ProblemError(503, detail="identity provider unavailable") from exc
