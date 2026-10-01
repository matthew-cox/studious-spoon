import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.e2e


def connect(settings, user: str, password: str) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=user,
        password=password,
        dbname="shortener",
        autocommit=True,
    )


def test_migrations_applied_and_api_user_can_read_links(e2e_settings):
    with connect(e2e_settings, "api_user", e2e_settings.api_db_password) as conn:
        version = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
        conn.execute("SELECT count(*) FROM public.links")
    assert version == ("0001",)


def test_admin_user_cannot_read_links(e2e_settings):
    with (
        connect(e2e_settings, "admin_user", e2e_settings.admin_db_password) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute("SELECT * FROM public.links")
