"""App factory and production wiring."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from shortener_admin.api_client import ApiClient, ApiError
from shortener_admin.auth import SID_COOKIE, CsrfFailed, LoginRequired, NoAccess, return_path
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.oidc import KeycloakOidc
from shortener_admin.routes import auth, health, links, pages
from shortener_admin.sessions import PostgresSessionStore
from shortener_admin.settings import AdminSettings, load_admin_settings
from shortener_admin.views import is_htmx, render

logger = logging.getLogger(__name__)
PACKAGE_DIR = Path(__file__).parent


def _as_of(value: str) -> str:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    templates.env.filters["as_of"] = _as_of
    return templates


def _login_url(next_path: str, expired: bool) -> str:
    url = f"/auth/login?next={quote(next_path, safe='/')}"
    return url + "&expired=1" if expired else url


def _to_login(request: Request, next_path: str, expired: bool) -> Response:
    url = _login_url(next_path, expired)
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def create_app(deps: AdminDeps) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if deps.aclose is not None:
                await deps.aclose()

    app = FastAPI(
        title="Shortener admin",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
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
    app.include_router(pages.router)
    app.include_router(links.router)

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
            return _to_login(request, return_path(request), expired=True)
        return render(request, "error.html", status_code=error.status, title=error.title,
                      message=error.detail or "")  # fmt: skip

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: Exception) -> Response:
        status = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
        title = {404: "Page not found", 405: "Not allowed"}.get(status, "Error")
        response = render(request, "error.html", status_code=status, title=title, message="")
        if isinstance(exc, StarletteHTTPException) and exc.headers:
            response.headers.update(exc.headers)  # e.g. Allow on 405
        return response

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        logger.exception("unhandled error")
        return render(request, "error.html", status_code=500, title="Something went wrong",
                      message="Please try again.")  # fmt: skip

    return app


def utc_now() -> datetime:
    return datetime.now(UTC)


def build_deps(settings: AdminSettings) -> AdminDeps:
    """Real implementations. Nothing here touches the network until first use."""
    engine = create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    api_http = httpx.AsyncClient(
        base_url=str(settings.api_base_url), timeout=settings.http_timeout_seconds
    )

    async def aclose() -> None:
        await api_http.aclose()
        await engine.dispose()

    return AdminDeps(
        settings=settings,
        sessions=PostgresSessionStore(engine),
        oidc=KeycloakOidc(settings, clock=utc_now),
        api=ApiClient(api_http),
        clock=utc_now,
        templates=make_templates(),
        aclose=aclose,
    )


def create_app_from_env() -> FastAPI:
    """uvicorn --factory entrypoint."""
    return create_app(build_deps(load_admin_settings()))
