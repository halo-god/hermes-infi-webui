"""Add profile_id to scheduled_tasks so the task runs with the selected
assistant's persona (HERMES_HOME + system_prompt), not just the bare CLI.

Revision ID: 0070
Revises: 0069
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_tasks",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scheduled_tasks", "profile_id")
