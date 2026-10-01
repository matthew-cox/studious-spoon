"""default privileges: tables created later by migrator inherit the per-role grants (spec §3.4)

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DEFAULTS = [
    ("public", "SELECT, INSERT, UPDATE, DELETE", "api_user"),
    ("analytics", "SELECT", "api_user"),
    ("analytics", "SELECT, INSERT, UPDATE, DELETE", "processor_user"),
    ("admin", "SELECT, INSERT, UPDATE, DELETE", "admin_user"),
]


def upgrade() -> None:
    for schema, privileges, role in DEFAULTS:
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA {schema} "
            f"GRANT {privileges} ON TABLES TO {role}"
        )


def downgrade() -> None:
    for schema, privileges, role in DEFAULTS:
        op.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA {schema} "
            f"REVOKE {privileges} ON TABLES FROM {role}"
        )
