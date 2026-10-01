import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from shortener_admin.api_client import ApiError
from shortener_admin.auth import SID_COOKIE, set_sid_cookie, verify_csrf
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.oidc import OidcError
from shortener_admin.security import safe_next_path
from shortener_admin.sessions import Session
from shortener_admin.views import render

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")
Deps = Annotated[AdminDeps, Depends(get_deps)]


@router.get("/login")
async def login(
    request: Request, deps: Deps, next: str = "/", expired: str | None = None
) -> Response:
    next_path = safe_next_path(next)
    if expired == "1":
        return render(
            request,
            "session_expired.html",
            login_url=f"/auth/login?next={quote(next_path, safe='/')}",
        )
    try:
        return await deps.oidc.begin_login(request, next_path)
    except OidcError:
        logger.exception("cannot start login")
        return render(request, "login_failed.html", status_code=503)


@router.get("/callback")
async def callback(request: Request, deps: Deps) -> Response:
    try:
        tokens, next_path = await deps.oidc.complete_login(request)
        me = await deps.api.me(tokens.access_token)
    except (OidcError, ApiError):
        logger.warning("login callback failed", exc_info=True)
        return render(request, "login_failed.html", status_code=400)
    now = deps.clock()
    await deps.sessions.purge_expired(now)
    if previous := request.cookies.get(SID_COOKIE):
        await deps.sessions.delete(previous)
    session = await deps.sessions.create(
        sub=me["sub"], username=me["username"], roles=frozenset(me["roles"]), tokens=tokens, now=now
    )
    response = RedirectResponse(next_path, status_code=303)
    set_sid_cookie(response, session.id, deps.settings)
    return response


@router.post("/logout")
async def logout(deps: Deps, session: Annotated[Session, Depends(verify_csrf)]) -> Response:
    home = f"{str(deps.settings.public_base_url).rstrip('/')}/"
    try:
        target = await deps.oidc.end_session_url(session.tokens.id_token, home)
    except OidcError:
        target = "/"
    await deps.sessions.delete(session.id)
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(SID_COOKIE, path="/")
    return response
