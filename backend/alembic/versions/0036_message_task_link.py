"""messages.task_id — persistent "discuss a task" link

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_task_id",
        "messages",
        "project_tasks",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_messages_task_id", "messages", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_task_id", table_name="messages")
    op.drop_constraint("fk_messages_task_id", "messages", type_="foreignkey")
    op.drop_column("messages", "task_id")
