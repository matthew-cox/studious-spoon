"""Session, access, and CSRF dependencies (spec §7.4, §8)."""

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import Response

from shortener_admin.api_client import ApiError
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.oidc import OidcError
from shortener_admin.security import tokens_match
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
        raise LoginRequired(current_path(request))
    if session.tokens.access_expires_at - now <= REFRESH_MARGIN:
        try:
            tokens = await deps.oidc.refresh(session.tokens.refresh_token, session.tokens.id_token)
            me = await deps.api.me(tokens.access_token)
        except (OidcError, ApiError) as exc:
            await deps.sessions.delete(session.id)
            raise LoginRequired(current_path(request), expired=True) from exc
        refreshed = await deps.sessions.update_tokens(
            session.id, tokens=tokens, roles=frozenset(me["roles"]), now=now
        )
        if refreshed is None:
            raise LoginRequired(current_path(request), expired=True)
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
