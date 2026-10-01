from fastapi import FastAPI

from shortener_api.deps import AppDeps
from shortener_api.errors import install_error_handlers
from shortener_api.routes import health, links


def create_app(deps: AppDeps) -> FastAPI:
    app = FastAPI(title="URL Shortener API", version=deps.settings.service_version)
    app.state.deps = deps
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(links.router)
    return app
