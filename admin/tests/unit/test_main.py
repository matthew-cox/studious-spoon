import functools

import pytest

from shortener_admin.api_client import ApiClient
from shortener_admin.main import build_deps, create_app_from_env
from shortener_admin.oidc import KeycloakOidc
from shortener_admin.sessions import PostgresSessionStore

ENV = {
    "DATABASE_URL": "postgresql+psycopg://admin_user:pw@127.0.0.1:1/shortener",
    "API_BASE_URL": "http://api:8000",
    "PUBLIC_BASE_URL": "http://localhost:8001",
    "OIDC_INTERNAL_URL": "http://keycloak:8080/realms/shortener",
    "OIDC_CLIENT_SECRET": "secret",
    "COOKIE_SECRET": "c" * 32,
}


def test_build_deps_uses_real_components_without_network(settings):
    deps = build_deps(settings, install_globals=False)
    assert isinstance(deps.sessions, PostgresSessionStore)
    assert isinstance(deps.oidc, KeycloakOidc)
    assert isinstance(deps.api, ApiClient)
    assert deps.clock().tzinfo is not None
    assert deps.tracer_provider is not None
    assert deps.tracer_provider.resource.attributes["service.name"] == "shortener-admin"
    assert deps.telemetry_shutdown is not None
    deps.telemetry_shutdown()


def test_create_app_from_env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(  # never install process-global OTel providers from tests
        "shortener_admin.main.build_deps", functools.partial(build_deps, install_globals=False)
    )
    app = create_app_from_env()
    assert app.title == "Shortener admin"
    app.state.deps.telemetry_shutdown()


def test_create_app_from_env_fails_fast(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(Exception, match="database_url"):
        create_app_from_env()
