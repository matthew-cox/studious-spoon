"""Session, access, and CSRF dependencies (spec §7.4, §8)."""

from datetime import timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from fastapi.responses import Response

from shortener_admin.api_client import ApiError
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.oidc import OidcError
from shortener_admin.security import safe_next_path, tokens_match
from shortener_admin.sessions import Session
from shortener_admin.settings import AdminSettings

SID_COOKIE = "sid"
REFRESH_MARGIN = timedelta(seconds=30)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class LoginRequired(Exception):
    def __init__(self, next_path: str, expired: bool = False) -> None:
        super().__init__(next_path)
        self.next_path = next_path
        self.expired = expired


class NoAccess(Exception):
    pass


class CsrfFailed(Exception):
    pass


def current_path(request: Request) -> str:
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


def _same_origin_path(url: str, public_base_url: str) -> str:
    """Reduce an absolute URL on our own origin to path+query; anything else becomes `/`."""
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        own = urlsplit(public_base_url)
        if (parts.scheme, parts.netloc) != (own.scheme, own.netloc):
            return "/"
    return parts.path + (f"?{parts.query}" if parts.query else "")


def return_path(request: Request) -> str:
    """Where to send the user after (re-)login: the page they were on, never a POST-only URL."""
    if request.method in ("GET", "HEAD"):
        return safe_next_path(current_path(request))
    public = str(get_deps(request).settings.public_base_url)
    for header in ("hx-current-url", "referer"):
        value = request.headers.get(header)
        if value:
            return safe_next_path(_same_origin_path(value, public))
    return "/"


def set_sid_cookie(response: Response, session_id: str, settings: AdminSettings) -> None:
    response.set_cookie(
        SID_COOKIE,
        session_id,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


async def require_session(
    request: Request, deps: Annotated[AdminDeps, Depends(get_deps)]
) -> Session:
    sid = request.cookies.get(SID_COOKIE)
    now = deps.clock()
    session = await deps.sessions.get(sid, now) if sid else None
    if session is None:
        raise LoginRequired(return_path(request))
    if session.tokens.access_expires_at - now <= REFRESH_MARGIN:
        try:
            tokens = await deps.oidc.refresh(session.tokens.refresh_token, session.tokens.id_token)
            me = await deps.api.me(tokens.access_token)
        except OidcError as exc:
            if exc.transient:  # Keycloak is down, not the session: keep it and say so
                raise ApiError(503, "Sign-in service unavailable") from exc
            await deps.sessions.delete(session.id)
            raise LoginRequired(return_path(request), expired=True) from exc
        except ApiError as exc:
            if exc.status != 401:
                raise
            await deps.sessions.delete(session.id)
            raise LoginRequired(return_path(request), expired=True) from exc
        refreshed = await deps.sessions.update_tokens(
            session.id, tokens=tokens, roles=frozenset(me["roles"]), now=now
        )
        if refreshed is None:
            raise LoginRequired(return_path(request), expired=True)
        session = refreshed
    request.state.session = session
    return session


async def require_access(session: Annotated[Session, Depends(require_session)]) -> Session:
    if not session.has_access:
        raise NoAccess()
    return session


async def verify_csrf(
    request: Request, session: Annotated[Session, Depends(require_session)]
) -> Session:
    if request.method in UNSAFE_METHODS:
        supplied = request.headers.get("x-csrf-token")
        if not supplied:
            form = await request.form()
            value = form.get("csrf_token")
            supplied = value if isinstance(value, str) else None
        if not tokens_match(session.csrf_token, supplied):
            raise CsrfFailed()
    return session


async def verify_csrf_with_access(session: Annotated[Session, Depends(verify_csrf)]) -> Session:
    if not session.has_access:
        raise NoAccess()
    return session
