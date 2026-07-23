"""Add max_iterations circuit-breaker column to profiles.

Gives each Profile a configurable cap on how many tool_call events a single
turn may emit before the runner forcibly cancels the ACP session. Previously
there was no application-level iteration limit — only a 900s hard timeout —
so a runaway ReAct loop could burn tokens and hog a concurrency slot.

Revision ID: 0056
Revises: 0055
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("max_iterations", sa.Integer(), nullable=False, server_default="50"),
    )


def downgrade() -> None:
    op.drop_column("profiles", "max_iterations")
