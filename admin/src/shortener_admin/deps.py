from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from fastapi import Request
from fastapi.templating import Jinja2Templates
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from shortener_admin.api_client import ApiClient
from shortener_admin.oidc import KeycloakOidc
from shortener_admin.sessions import SessionStore
from shortener_admin.settings import AdminSettings


@dataclass(kw_only=True)
class AdminDeps:
    settings: AdminSettings
    sessions: SessionStore
    oidc: KeycloakOidc
    api: ApiClient
    clock: Callable[[], datetime]
    templates: Jinja2Templates
    aclose: Callable[[], Awaitable[None]] | None = None  # releases engine / HTTP client
    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    telemetry_shutdown: Callable[[], None] | None = None


def get_deps(request: Request) -> AdminDeps:
    return cast(AdminDeps, request.app.state.deps)
