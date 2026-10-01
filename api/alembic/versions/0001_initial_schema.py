"""initial schema: links, analytics rollups, admin sessions, grants

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)

GRANTS = [
    # api_user: read/write link data, read-only analytics (spec §3.4)
    "GRANT USAGE ON SCHEMA public TO api_user, processor_user",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON public.links TO api_user",
    "GRANT USAGE ON SCHEMA analytics TO api_user, processor_user",
    "GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO api_user",
    # processor_user: write analytics, read only (id, code) of links
    "GRANT SELECT (id, code) ON public.links TO processor_user",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics TO processor_user",
    # admin_user: admin schema only
    "GRANT USAGE ON SCHEMA admin TO admin_user",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON admin.sessions TO admin_user",
    # read-only visibility of the schema version (readiness/e2e checks); alembic creates
    # alembic_version before running this revision, so the grant succeeds
    "GRANT SELECT ON public.alembic_version TO api_user",
]


def upgrade() -> None:
    op.execute("CREATE SCHEMA analytics")
    op.execute("CREATE SCHEMA admin")

    op.create_table(
        "links",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("owner_sub", sa.String(64), nullable=False),
        sa.Column("owner_username", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("blocked_at", TZ, nullable=True),
        sa.Column("blocked_by", sa.String(64), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_links_code"),
        sa.CheckConstraint(
            "blocked_at IS NULL OR (blocked_by IS NOT NULL AND blocked_reason IS NOT NULL)",
            name="ck_links_block_fields",
        ),
        schema="public",
    )
    op.create_index("ix_links_owner_sub", "links", ["owner_sub"], schema="public")

    op.create_table(
        "link_clicks_hourly",
        sa.Column(
            "link_id",
            sa.Uuid(),
            sa.ForeignKey("public.links.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("bucket_start", TZ, primary_key=True),
        sa.Column("count", sa.BigInteger(), nullable=False),
        schema="analytics",
    )
    op.create_table(
        "link_referrers_daily",
        sa.Column(
            "link_id",
            sa.Uuid(),
            sa.ForeignKey("public.links.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("bucket_date", sa.Date(), primary_key=True),
        sa.Column("referrer_host", sa.Text(), primary_key=True),
        sa.Column("count", sa.BigInteger(), nullable=False),
        schema="analytics",
    )
    op.create_table(
        "pipeline_status",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("last_committed_at", TZ, nullable=True),
        sa.CheckConstraint("id = 1", name="ck_pipeline_status_single_row"),
        schema="analytics",
    )
    op.execute("INSERT INTO analytics.pipeline_status (id) VALUES (1)")

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("sub", sa.String(64), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("id_token", sa.Text(), nullable=False),
        sa.Column("access_expires_at", TZ, nullable=False),
        sa.Column("refresh_expires_at", TZ, nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", TZ, nullable=False, server_default=sa.func.now()),
        schema="admin",
    )

    for statement in GRANTS:
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("sessions", schema="admin")
    op.drop_table("pipeline_status", schema="analytics")
    op.drop_table("link_referrers_daily", schema="analytics")
    op.drop_table("link_clicks_hourly", schema="analytics")
    op.drop_index("ix_links_owner_sub", table_name="links", schema="public")
    op.drop_table("links", schema="public")
    op.execute("DROP SCHEMA admin")
    op.execute("DROP SCHEMA analytics")
