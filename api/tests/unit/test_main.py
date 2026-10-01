import functools

import pytest

from shortener_api.auth import TokenValidator
from shortener_api.main import build_deps, create_app, create_app_from_env
from shortener_api.publisher import BufferedClickPublisher

ENV = {
    "DATABASE_URL": "postgresql+psycopg://api_user:pw@127.0.0.1:1/shortener",
    "PUBLIC_BASE_URL": "http://localhost:8000",
    "OIDC_ISSUER": "http://localhost:8080/realms/shortener",
    "OIDC_INTERNAL_URL": "http://keycloak:8080/realms/shortener",
    "SQS_ENDPOINT_URL": "http://127.0.0.1:1",
    "AWS_ACCESS_KEY_ID": "local",
    "AWS_SECRET_ACCESS_KEY": "local",
}


class SpyPublisher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def publish(self, event) -> None:
        self.calls.append("publish")

    async def start(self) -> None:
        self.calls.append("start")

    async def stop(self) -> None:
        self.calls.append("stop")


async def test_lifespan_starts_and_stops_the_publisher(deps):
    spy = SpyPublisher()
    deps.publisher = spy
    app = create_app(deps)
    async with app.router.lifespan_context(app):
        assert spy.calls == ["start"]
    assert spy.calls == ["start", "stop"]


async def test_build_deps_uses_real_components_without_network(settings):
    deps = build_deps(settings, install_globals=False)
    assert isinstance(deps.publisher, BufferedClickPublisher)
    assert isinstance(deps.token_validator, TokenValidator)
    assert deps.tracer_provider is not None
    assert deps.tracer_provider.resource.attributes["service.name"] == "shortener-api"
    assert deps.telemetry_shutdown is not None
    deps.telemetry_shutdown()
    await deps.engine.dispose()


def test_create_app_from_env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(  # never install process-global OTel providers from tests
        "shortener_api.main.build_deps", functools.partial(build_deps, install_globals=False)
    )
    app = create_app_from_env()
    assert app.title == "URL Shortener API"


def test_create_app_from_env_fails_fast_without_config(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(Exception, match="database_url"):
        create_app_from_env()


async def test_sqs_client_has_bounded_timeouts_and_retries(settings):
    deps = build_deps(settings, install_globals=False)
    config = deps.publisher._sender._client.meta.config  # private: no public accessor
    assert config.connect_timeout == 2
    assert config.read_timeout == 5
    assert config.retries["total_max_attempts"] == 1  # no botocore retries: the publisher owns them
    assert config.retries["mode"] == "standard"
    await deps.engine.dispose()
