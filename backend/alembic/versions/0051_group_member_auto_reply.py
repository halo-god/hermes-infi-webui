"""group_members: auto_reply — per-assistant auto-reply toggle for group chat

Replaces the old all-or-nothing channel_mode="always" fan-out (every AI
member replied to every message) with a per-member flag: an AI member now
replies when it is @-mentioned OR has auto_reply=true. Backfills auto_reply
for existing conversations that already relied on channel_mode="always" so
their behavior doesn't change the moment this migration runs.

Revision ID: 0051
Revises: 0050
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "group_members",
        sa.Column("auto_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE group_members gm
        SET auto_reply = true
        FROM conversations c
        WHERE gm.conversation_id = c.id
          AND c.channel_mode = 'always'
          AND gm.agent_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("group_members", "auto_reply")
