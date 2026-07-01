"""fix workspace_file_versions.created_at to be timezone-aware

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-01
"""
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # created_at was created as a naive TIMESTAMP (see 0009_workspace_versions.py),
    # unlike every other timestamped table in this app (Timestamps mixin uses
    # DateTime(timezone=True)). The app/DB have always run in UTC (no TZ env
    # override anywhere), so reinterpreting the naive value as UTC is correct.
    op.execute(
        "ALTER TABLE workspace_file_versions "
        "ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC'"
    )
    op.execute(
        "ALTER TABLE workspace_file_versions ALTER COLUMN created_at SET DEFAULT now()"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE workspace_file_versions "
        "ALTER COLUMN created_at TYPE timestamp USING created_at AT TIME ZONE 'UTC'"
    )
