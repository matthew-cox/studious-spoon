from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrateSettings(BaseSettings):
    """Configuration for the `migrate` release job (alembic)."""

    model_config = SettingsConfigDict(extra="ignore")

    migrator_database_url: PostgresDsn


def load_migrate_settings() -> MigrateSettings:
    return MigrateSettings()
