from pydantic import AnyHttpUrl, Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrateSettings(BaseSettings):
    """Configuration for the `migrate` release job (alembic)."""

    model_config = SettingsConfigDict(extra="ignore")

    migrator_database_url: PostgresDsn


def load_migrate_settings() -> MigrateSettings:
    return MigrateSettings()


class ApiSettings(BaseSettings):
    """Configuration for the API service (environment only, spec §15.1 III)."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: PostgresDsn
    public_base_url: AnyHttpUrl
    # Exact string compared against the token's `iss`; a trailing slash would never match.
    oidc_issuer: str = Field(pattern=r"^https?://\S+[^/]$")
    oidc_internal_url: str = Field(pattern=r"^https?://\S+[^/]$")
    oidc_audience: str = "shortener-api"
    sqs_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    click_events_queue_name: str = "click-events"
    click_buffer_size: int = Field(default=10_000, ge=1)
    click_flush_interval_seconds: float = Field(default=0.25, gt=0)
    service_version: str = "dev"


def load_api_settings() -> ApiSettings:
    return ApiSettings()
