"""SQLAlchemy Core mirror of the Alembic schema (api/alembic/versions). Migrations own the DDL."""

import sqlalchemy as sa

metadata = sa.MetaData()
TZ = sa.DateTime(timezone=True)

links = sa.Table(
    "links",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.func.gen_random_uuid()),
    sa.Column("code", sa.String(32), nullable=False),
    sa.Column("target_url", sa.Text, nullable=False),
    sa.Column("owner_sub", sa.String(64), nullable=False),
    sa.Column("owner_username", sa.String(255), nullable=False),
    sa.Column("is_active", sa.Boolean, nullable=False),
    sa.Column("blocked_at", TZ),
    sa.Column("blocked_by", sa.String(64)),
    sa.Column("blocked_reason", sa.Text),
    sa.Column("created_at", TZ, nullable=False),
    sa.Column("updated_at", TZ, nullable=False),
    schema="public",
)

link_events = sa.Table(
    "link_events",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("link_id", sa.Uuid, nullable=False),
    sa.Column("link_code", sa.String(32), nullable=False),
    sa.Column("action", sa.String(16), nullable=False),
    sa.Column("actor_sub", sa.String(64), nullable=False),
    sa.Column("actor_username", sa.String(255), nullable=False),
    sa.Column("reason", sa.Text),
    sa.Column("occurred_at", TZ, nullable=False),
    schema="public",
)

link_clicks_hourly = sa.Table(
    "link_clicks_hourly",
    metadata,
    sa.Column("link_id", sa.Uuid, primary_key=True),
    sa.Column("bucket_start", TZ, primary_key=True),
    sa.Column("count", sa.BigInteger, nullable=False),
    schema="analytics",
)

link_referrers_daily = sa.Table(
    "link_referrers_daily",
    metadata,
    sa.Column("link_id", sa.Uuid, primary_key=True),
    sa.Column("bucket_date", sa.Date, primary_key=True),
    sa.Column("referrer_host", sa.Text, primary_key=True),
    sa.Column("count", sa.BigInteger, nullable=False),
    schema="analytics",
)

pipeline_status = sa.Table(
    "pipeline_status",
    metadata,
    sa.Column("id", sa.SmallInteger, primary_key=True),
    sa.Column("last_committed_at", TZ),
    schema="analytics",
)
