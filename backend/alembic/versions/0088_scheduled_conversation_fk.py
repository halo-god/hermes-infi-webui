"""Add the missing scheduled_tasks.conversation_id foreign key.

The ORM model declares ForeignKey("conversations.id", ondelete="SET NULL")
but no migration ever created it — deleting a task's dedicated conversation
left task.conversation_id pointing at a dead id. The runner's
_get_or_create_conversation now detects the missing conversation and
recreates it, but the FK keeps the DB consistent for any other code path.

Revision ID: 0088
Revises: 0087
Create Date: 2026-08-12
"""
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE scheduled_tasks "
        "ADD CONSTRAINT scheduled_tasks_conversation_id_fkey "
        "FOREIGN KEY (conversation_id) REFERENCES conversations(id) "
        "ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE scheduled_tasks DROP CONSTRAINT scheduled_tasks_conversation_id_fkey"
    )
