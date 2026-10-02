"""link_events: append-only moderation history (who blocked, unblocked or deleted a link)

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "link_events",
        # Identity, not a UUID: it also orders events that share a timestamp.
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        # No foreign key: the record must outlive a deleted link.
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("link_code", sa.String(32), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_sub", sa.String(64), nullable=False),
        # Stored at write time so the record stays readable if the user changes in Keycloak.
        sa.Column("actor_username", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", TZ, nullable=False),
        sa.CheckConstraint(
            "action IN ('block', 'unblock', 'delete')", name="ck_link_events_action"
        ),
        schema="public",
    )
    op.create_index(
        "ix_link_events_link_id", "link_events", ["link_id", "occurred_at"], schema="public"
    )
    # Default privileges (0002) gave api_user full read/write; the history is append-only.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON public.link_events FROM api_user")


def downgrade() -> None:
    op.drop_index("ix_link_events_link_id", table_name="link_events", schema="public")
    op.drop_table("link_events", schema="public")
