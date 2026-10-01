from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class E2ESettings(BaseSettings):
    """Reads the same .env that docker compose uses, plus host-side URLs."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    api_db_password: str
    admin_db_password: str
    keycloak_url: str = "http://localhost:8080"
    keycloak_realm: str = "shortener"
    keycloak_admin_user: str
    keycloak_admin_password: str
    shortener_admin_client_secret: str
    sqs_endpoint_url: str = "http://localhost:9324"
    grafana_url: str = "http://localhost:3000"
    otlp_http_url: str = "http://localhost:4318"
    api_url: str = "http://localhost:8000"


@pytest.fixture(scope="session")
def e2e_settings() -> E2ESettings:
    return E2ESettings()
