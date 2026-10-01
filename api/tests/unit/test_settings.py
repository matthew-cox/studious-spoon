import pytest
from pydantic import ValidationError

from shortener_api.settings import load_migrate_settings


def test_missing_url_fails_fast_naming_the_variable(monkeypatch):
    monkeypatch.delenv("MIGRATOR_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="migrator_database_url"):
        load_migrate_settings()


def test_reads_url_from_environment(monkeypatch):
    url = "postgresql+psycopg://migrator:pw@db:5432/shortener"
    monkeypatch.setenv("MIGRATOR_DATABASE_URL", url)
    assert str(load_migrate_settings().migrator_database_url) == url


def test_rejects_non_postgres_url(monkeypatch):
    monkeypatch.setenv("MIGRATOR_DATABASE_URL", "mysql://root@db/shortener")
    with pytest.raises(ValidationError):
        load_migrate_settings()
