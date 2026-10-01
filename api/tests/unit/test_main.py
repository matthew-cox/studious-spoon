import pytest

from shortener_api.auth import TokenValidator
from shortener_api.main import build_deps, create_app, create_app_from_env
from shortener_api.publisher import BufferedClickPublisher
from shortener_api.telemetry import configure_meter_provider

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
    deps = build_deps(settings)
    assert isinstance(deps.publisher, BufferedClickPublisher)
    assert isinstance(deps.token_validator, TokenValidator)
    await deps.engine.dispose()


def test_create_app_from_env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    app = create_app_from_env()
    assert app.title == "URL Shortener API"


def test_create_app_from_env_fails_fast_without_config(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(Exception, match="database_url"):
        create_app_from_env()


@pytest.mark.parametrize("endpoint", [None, "http://127.0.0.1:1"])
def test_meter_provider_carries_resource_attributes(settings, endpoint):
    settings = settings.model_copy(update={"otel_exporter_otlp_endpoint": endpoint})
    provider = configure_meter_provider(settings)
    attributes = provider._sdk_config.resource.attributes  # private: the SDK has no public accessor
    assert attributes["service.name"] == "shortener-api"
    assert attributes["deployment.environment"] == "local"
    provider.shutdown()


async def test_sqs_client_has_bounded_timeouts_and_retries(settings):
    deps = build_deps(settings)
    config = deps.publisher._sender._client.meta.config  # private: no public accessor
    assert config.connect_timeout == 2
    assert config.read_timeout == 5
    assert config.retries["total_max_attempts"] == 2  # max_attempts=1 retry
    assert config.retries["mode"] == "standard"
    await deps.engine.dispose()
