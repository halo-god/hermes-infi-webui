"""Scheduled task execution modes + run tracking.

Adds `execution_mode` (existing | new_conversation) to scheduled_tasks:
- "existing": reuse the task's dedicated conversation (context preserved)
- "new_conversation": spin up a fresh conversation per run (periodic reports)

Also adds `run_count` for stats and `missed` handling support.

Revision ID: 0078
Revises: 0077
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_tasks",
        sa.Column("execution_mode", sa.String(24), nullable=False, server_default="existing"),
    )
    op.add_column(
        "scheduled_tasks",
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("scheduled_tasks", "run_count")
    op.drop_column("scheduled_tasks", "execution_mode")
