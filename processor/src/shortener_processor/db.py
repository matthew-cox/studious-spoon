"""The tables the processor touches, mirroring api/alembic (migrations own the DDL).
processor_user may read only links.id and links.code."""

import sqlalchemy as sa

metadata = sa.MetaData()
TZ = sa.DateTime(timezone=True)

links = sa.Table(
    "links",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("code", sa.String(32), nullable=False),
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
