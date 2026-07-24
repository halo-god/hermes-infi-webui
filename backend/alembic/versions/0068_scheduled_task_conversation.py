"""Add conversation_id to scheduled_tasks.

Each scheduled task gets a dedicated conversation (type="scheduled") on first
execution, so the agent's response is persisted as a browsable message history
instead of being discarded. Users access it via the notification link.

Revision ID: 0068
Revises: 0067
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_tasks",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scheduled_tasks", "conversation_id")
