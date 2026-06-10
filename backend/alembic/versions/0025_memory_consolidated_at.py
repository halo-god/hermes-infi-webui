"""add last_consolidated_at to agent_memory

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_memory",
        sa.Column("last_consolidated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_memory", "last_consolidated_at")
