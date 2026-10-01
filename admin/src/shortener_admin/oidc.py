"""Keycloak login via Authlib (spec §7.4). This module is the only place that knows about
Authlib; every library error becomes OidcError."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode

import httpx
from authlib.integrations.base_client import OAuthError
from authlib.integrations.starlette_client import OAuth
from joserfc.errors import JoseError
from starlette.requests import Request
from starlette.responses import Response

from shortener_admin.security import safe_next_path
from shortener_admin.sessions import TokenSet
from shortener_admin.settings import AdminSettings

logger = logging.getLogger(__name__)
_LIBRARY_ERRORS = (OAuthError, JoseError, httpx.HTTPError, KeyError, ValueError, TypeError)


class OidcError(Exception):
    """Login or refresh failed. The reason is logged; users see a generic message."""


class KeycloakOidc:
    def __init__(self, settings: AdminSettings, *, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._client_id = settings.oidc_client_id
        self._redirect_uri = settings.redirect_uri
        oauth = OAuth()
        oauth.register(
            name="keycloak",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret.get_secret_value(),
            server_metadata_url=f"{settings.oidc_internal_url}/.well-known/openid-configuration",
            client_kwargs={
                "scope": "openid",
                "code_challenge_method": "S256",
                "timeout": settings.http_timeout_seconds,
            },
        )
        self._app = cast(Any, oauth.create_client("keycloak"))

    def _token_set(self, token: dict[str, Any], previous_id_token: str = "") -> TokenSet:
        now = self._clock()
        access_in = int(token.get("expires_in") or 0)
        refresh_in = int(token.get("refresh_expires_in") or access_in)
        return TokenSet(
            access_token=str(token["access_token"]),
            refresh_token=str(token["refresh_token"]),
            id_token=str(token.get("id_token") or previous_id_token),
            access_expires_at=now + timedelta(seconds=access_in),
            refresh_expires_at=now + timedelta(seconds=refresh_in),
        )

    async def begin_login(self, request: Request, next_path: str) -> Response:
        request.session["next"] = safe_next_path(next_path)
        try:
            return cast(Response, await self._app.authorize_redirect(request, self._redirect_uri))
        except _LIBRARY_ERRORS as exc:
            raise OidcError(f"cannot start login: {exc}") from exc

    async def complete_login(self, request: Request) -> tuple[TokenSet, str]:
        next_path = safe_next_path(request.session.get("next"))
        try:
            token = await self._app.authorize_access_token(request)
            if "userinfo" not in token:  # Authlib only sets it after validating the ID token
                raise OidcError("no validated id token in response")
            # Authlib validates `azp` but never checks that `aud` names this client.
            audience = token["userinfo"].get("aud")
            audiences = [audience] if isinstance(audience, str) else list(audience or [])
            if self._client_id not in audiences:
                raise OidcError("id token audience does not include this client")
            return self._token_set(dict(token)), next_path
        except _LIBRARY_ERRORS as exc:
            logger.warning("login callback rejected: %s", exc)
            raise OidcError(str(exc)) from exc
        finally:
            request.session.clear()

    async def refresh(self, refresh_token: str, previous_id_token: str) -> TokenSet:
        try:
            token = await self._app.fetch_access_token(
                grant_type="refresh_token", refresh_token=refresh_token
            )
            return self._token_set(dict(token), previous_id_token)
        except _LIBRARY_ERRORS as exc:
            raise OidcError(f"refresh failed: {exc}") from exc

    async def end_session_url(self, id_token_hint: str, post_logout_redirect_uri: str) -> str:
        try:
            metadata = await self._app.load_server_metadata()
            endpoint = str(metadata["end_session_endpoint"])
        except _LIBRARY_ERRORS as exc:
            raise OidcError(f"cannot build logout url: {exc}") from exc
        params = {
            "id_token_hint": id_token_hint,
            "post_logout_redirect_uri": post_logout_redirect_uri,
            "client_id": self._client_id,
        }
        return f"{endpoint}?{urlencode(params)}"
