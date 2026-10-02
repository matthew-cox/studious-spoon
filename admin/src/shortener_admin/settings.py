from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_NO_TRAILING_SLASH = r"^https?://\S+[^/]$"


class AdminSettings(BaseSettings):
    """Configuration for the admin UI (environment only, spec §15.1 III)."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: PostgresDsn
    api_base_url: AnyHttpUrl
    public_base_url: AnyHttpUrl
    # Discovery is fetched here; the issuer the ID token must match comes from discovery itself.
    oidc_internal_url: str = Field(pattern=_NO_TRAILING_SLASH)
    oidc_client_id: str = "shortener-admin"
    oidc_client_secret: SecretStr
    cookie_secret: SecretStr = Field(min_length=32)
    cookie_secure: bool = False
    http_timeout_seconds: float = Field(default=5.0, gt=0)
    otel_exporter_otlp_endpoint: str | None = None
    deployment_environment: str = "local"
    otel_metric_export_interval_ms: int = Field(default=10_000, ge=1_000)
    log_level: str = "INFO"
    service_version: str = "dev"
    # Browser-facing observability UI (Grafana dashboard); unset hides the admin nav link.
    observability_url: AnyHttpUrl | None = None

    @property
    def redirect_uri(self) -> str:
        return f"{str(self.public_base_url).rstrip('/')}/auth/callback"


def load_admin_settings() -> AdminSettings:
    return AdminSettings()
