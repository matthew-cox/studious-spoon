from datetime import UTC, datetime

import psycopg
import pytest
from alembic import command
from psycopg import errors

pytestmark = pytest.mark.integration


def tables(conn: psycopg.Connection, schema: str) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)
    ).fetchall()
    return {r[0] for r in rows}


def test_all_tables_exist(migrated):
    with migrated.connect("migrator") as conn:
        assert {"links", "alembic_version"} <= tables(conn, "public")
        assert tables(conn, "analytics") == {
            "link_clicks_hourly",
            "link_referrers_daily",
            "pipeline_status",
        }
        assert tables(conn, "admin") == {"sessions"}


def test_pipeline_status_is_seeded_with_single_row(migrated):
    with migrated.connect("migrator") as conn:
        rows = conn.execute(
            "SELECT id, last_committed_at FROM analytics.pipeline_status"
        ).fetchall()
    assert rows == [(1, None)]


def test_upgrade_is_idempotent(migrated, make_alembic_config):
    command.upgrade(make_alembic_config(migrated), "head")  # second run: no-op, no error


def test_code_is_unique(insert_link):
    insert_link(code="dupcode")
    with pytest.raises(errors.UniqueViolation):
        insert_link(code="dupcode")


def test_block_requires_by_and_reason(insert_link):
    with pytest.raises(errors.CheckViolation):
        insert_link(blocked_at=datetime.now(UTC))


def test_block_with_by_and_reason_is_allowed(insert_link):
    insert_link(blocked_at=datetime.now(UTC), blocked_by="sub-alice", blocked_reason="phishing")


def test_api_user_privileges(migrated, insert_link):
    link_id = insert_link()
    with migrated.connect("api_user") as conn:
        conn.execute("SELECT id, target_url FROM public.links WHERE id = %s", (link_id,))
        conn.execute("UPDATE public.links SET is_active = false WHERE id = %s", (link_id,))
        conn.execute("SELECT count(*) FROM analytics.link_clicks_hourly")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO analytics.link_clicks_hourly VALUES (%s, now(), 1)", (link_id,)
            )
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM admin.sessions")


def test_api_user_can_read_schema_version(migrated):
    with migrated.connect("api_user") as conn:
        version = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
    assert version == ("0002",)


def test_processor_user_privileges(migrated, insert_link):
    link_id = insert_link()
    with migrated.connect("processor_user") as conn:
        conn.execute("SELECT id, code FROM public.links WHERE id = %s", (link_id,))
        conn.execute(
            "INSERT INTO analytics.link_clicks_hourly VALUES (%s, date_trunc('hour', now()), 1)",
            (link_id,),
        )
        conn.execute("UPDATE analytics.pipeline_status SET last_committed_at = now() WHERE id = 1")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT target_url FROM public.links")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO public.links (code, target_url, owner_sub, owner_username) "
                "VALUES ('x1', 'https://x', 's', 'u')"
            )
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM admin.sessions")


def test_admin_user_is_confined_to_admin_schema(migrated):
    with migrated.connect("admin_user") as conn:
        conn.execute("SELECT count(*) FROM admin.sessions")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM public.links")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM analytics.link_clicks_hourly")


def test_deleting_a_link_cascades_to_rollups(migrated, insert_link):
    link_id = insert_link()
    with migrated.connect("migrator") as conn:
        conn.execute(
            "INSERT INTO analytics.link_clicks_hourly VALUES (%s, date_trunc('hour', now()), 3)",
            (link_id,),
        )
        conn.execute(
            "INSERT INTO analytics.link_referrers_daily VALUES (%s, current_date, '(direct)', 3)",
            (link_id,),
        )
    with migrated.connect("api_user") as conn:
        conn.execute("DELETE FROM public.links WHERE id = %s", (link_id,))
    with migrated.connect("migrator") as conn:
        hourly = conn.execute(
            "SELECT count(*) FROM analytics.link_clicks_hourly WHERE link_id = %s", (link_id,)
        ).fetchone()
        daily = conn.execute(
            "SELECT count(*) FROM analytics.link_referrers_daily WHERE link_id = %s", (link_id,)
        ).fetchone()
    assert hourly == (0,)
    assert daily == (0,)


def test_downgrade_and_upgrade_round_trip(pg_server, make_alembic_config):
    with pg_server.connect("postgres", db="postgres") as conn:
        conn.execute("CREATE DATABASE shortener_roundtrip OWNER migrator")
    cfg = make_alembic_config(pg_server, db="shortener_roundtrip")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with pg_server.connect("migrator", db="shortener_roundtrip") as conn:
        assert tables(conn, "admin") == {"sessions"}


def test_tables_created_by_later_migrations_inherit_role_grants(migrated):
    """Spec §3.4 grants whole schemas, so a table added later must not need hand-written grants."""
    with migrated.connect("migrator") as conn:
        conn.execute("CREATE TABLE analytics.future_rollup (n int)")
        conn.execute("CREATE TABLE public.future_links (n int)")
    try:
        with migrated.connect("processor_user") as conn:
            conn.execute("INSERT INTO analytics.future_rollup VALUES (1)")
        with migrated.connect("api_user") as conn:
            conn.execute("SELECT * FROM analytics.future_rollup")
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute("INSERT INTO analytics.future_rollup VALUES (2)")
            conn.execute("INSERT INTO public.future_links VALUES (1)")
            conn.execute("DELETE FROM public.future_links")
        with migrated.connect("admin_user") as conn, pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM analytics.future_rollup")
    finally:
        with migrated.connect("migrator") as conn:
            conn.execute("DROP TABLE analytics.future_rollup")
            conn.execute("DROP TABLE public.future_links")
