from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from fastapi import Request
from fastapi.templating import Jinja2Templates

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


def get_deps(request: Request) -> AdminDeps:
    return cast(AdminDeps, request.app.state.deps)
