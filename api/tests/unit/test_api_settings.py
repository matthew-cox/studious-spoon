import pytest
from pydantic import ValidationError

from shortener_api.settings import load_api_settings

REQUIRED = {
    "DATABASE_URL": "postgresql+psycopg://api_user:pw@db:5432/shortener",
    "PUBLIC_BASE_URL": "http://localhost:8000",
    "OIDC_ISSUER": "http://localhost:8080/realms/shortener",
    "OIDC_INTERNAL_URL": "http://keycloak:8080/realms/shortener",
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
        load_api_settings()


def test_defaults(env):
    settings = load_api_settings()
    assert settings.oidc_audience == "shortener-api"
    assert settings.click_events_queue_name == "click-events"
    assert settings.click_buffer_size == 10_000
    assert settings.sqs_endpoint_url is None


def test_issuer_with_trailing_slash_is_rejected(env):
    env.setenv("OIDC_ISSUER", "http://localhost:8080/realms/shortener/")
    with pytest.raises(ValidationError, match="oidc_issuer"):
        load_api_settings()


def test_non_positive_buffer_is_rejected(env):
    env.setenv("CLICK_BUFFER_SIZE", "0")
    with pytest.raises(ValidationError):
        load_api_settings()
