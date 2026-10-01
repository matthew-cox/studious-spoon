from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProcessorSettings(BaseSettings):
    """Configuration for the click processor (environment only, spec §15.1 III)."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: PostgresDsn
    sqs_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    click_events_queue_name: str = "click-events"
    click_events_dlq_name: str = "click-events-dlq"
    batch_max_messages: int = Field(default=100, ge=1, le=1000)
    batch_window_seconds: float = Field(default=1.0, gt=0)
    receive_wait_seconds: int = Field(default=20, ge=0, le=20)  # SQS long-poll maximum
    queue_depth_interval_seconds: float = Field(default=30.0, gt=0)
    link_cache_ttl_seconds: float = Field(default=60.0, ge=0)
    health_host: str = "0.0.0.0"  # noqa: S104 — the container healthcheck / load balancer must reach it
    health_port: int = 8002
    shutdown_grace_seconds: float = Field(default=25.0, gt=0)
    otel_exporter_otlp_endpoint: str | None = None
    deployment_environment: str = "local"
    service_version: str = "dev"


def load_processor_settings() -> ProcessorSettings:
    return ProcessorSettings()
