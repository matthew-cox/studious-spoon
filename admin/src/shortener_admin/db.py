"""admin.sessions mirror (api/alembic owns the DDL). admin_user may touch nothing else."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()
TZ = sa.DateTime(timezone=True)

sessions = sa.Table(
    "sessions",
    metadata,
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
    sa.Column("created_at", TZ, nullable=False),
    sa.Column("last_seen_at", TZ, nullable=False),
    schema="admin",
)
