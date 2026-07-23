"""Add composite index for admin/usage queries on messages table.

Revision ID: 0055
Revises: 0054
Create Date: 2026-07-23
"""
from alembic import op
from sqlalchemy import text as sa_text

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # idx_messages_usage supports the admin/usage endpoint which filters by
    # role='agent' + created_at range and groups by date_trunc('day', created_at).
    idx_exists = conn.execute(
        sa_text("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_messages_usage'")
    ).scalar()
    if not idx_exists:
        op.create_index(
            "idx_messages_usage",
            "messages",
            ["created_at", "role", "owner_id"],
        )


def downgrade() -> None:
    op.drop_index("idx_messages_usage", table_name="messages")
