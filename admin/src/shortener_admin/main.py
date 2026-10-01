"""App factory and production wiring."""

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from shortener_admin.api_client import ApiError
from shortener_admin.auth import SID_COOKIE, CsrfFailed, LoginRequired, NoAccess
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.routes import auth, health
from shortener_admin.views import is_htmx, render

logger = logging.getLogger(__name__)
PACKAGE_DIR = Path(__file__).parent


def make_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=PACKAGE_DIR / "templates")


def _login_url(next_path: str, expired: bool) -> str:
    url = f"/auth/login?next={quote(next_path, safe='/')}"
    return url + "&expired=1" if expired else url


def _to_login(request: Request, next_path: str, expired: bool) -> Response:
    url = _login_url(next_path, expired)
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def create_app(deps: AdminDeps) -> FastAPI:
    app = FastAPI(title="Shortener admin", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps
    app.add_middleware(
        SessionMiddleware,
        secret_key=deps.settings.cookie_secret.get_secret_value(),
        session_cookie="login_state",
        path="/auth",
        max_age=600,
        same_site="lax",
        https_only=deps.settings.cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.include_router(health.router)
    app.include_router(auth.router)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, LoginRequired) else LoginRequired("/")
        return _to_login(request, error.next_path, error.expired)

    @app.exception_handler(NoAccess)
    async def _no_access(request: Request, exc: Exception) -> Response:
        return render(request, "no_access.html", status_code=403)

    @app.exception_handler(CsrfFailed)
    async def _csrf(request: Request, exc: Exception) -> Response:
        return render(request, "error.html", status_code=403, title="Request expired",
                      message="Your form expired. Reload the page and try again.")  # fmt: skip

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, ApiError) else ApiError(500, "Error")
        if error.status == 401:  # token revoked between refreshes
            if sid := request.cookies.get(SID_COOKIE):
                await get_deps(request).sessions.delete(sid)
            return _to_login(request, request.url.path, expired=True)
        return render(request, "error.html", status_code=error.status, title=error.title,
                      message=error.detail or "")  # fmt: skip

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        logger.exception("unhandled error")
        return render(request, "error.html", status_code=500, title="Something went wrong",
                      message="Please try again.")  # fmt: skip

    return app
