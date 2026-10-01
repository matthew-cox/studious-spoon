import pytest
from pydantic import ValidationError

from shortener_admin.settings import load_admin_settings

REQUIRED = {
    "DATABASE_URL": "postgresql+psycopg://admin_user:pw@db:5432/shortener",
    "API_BASE_URL": "http://api:8000",
    "PUBLIC_BASE_URL": "http://localhost:8001",
    "OIDC_INTERNAL_URL": "http://keycloak:8080/realms/shortener",
    "OIDC_CLIENT_SECRET": "client-secret",
    "COOKIE_SECRET": "x" * 32,
}


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_variable_fails_fast_naming_it(env, missing):
    env.delenv(missing)
    with pytest.raises(ValidationError, match=missing.lower()):
        load_admin_settings()


def test_defaults_and_redirect_uri(env):
    env.setenv("PUBLIC_BASE_URL", "http://localhost:8001/")
    settings = load_admin_settings()
    assert settings.oidc_client_id == "shortener-admin"
    assert settings.cookie_secure is False
    assert settings.redirect_uri == "http://localhost:8001/auth/callback"


def test_short_cookie_secret_is_rejected(env):
    env.setenv("COOKIE_SECRET", "too-short")
    with pytest.raises(ValidationError, match="cookie_secret"):
        load_admin_settings()


def test_trailing_slash_internal_url_is_rejected(env):
    env.setenv("OIDC_INTERNAL_URL", REQUIRED["OIDC_INTERNAL_URL"] + "/")
    with pytest.raises(ValidationError, match="oidc_internal_url"):
        load_admin_settings()


def test_secrets_are_not_printed(env):
    assert "client-secret" not in repr(load_admin_settings())
