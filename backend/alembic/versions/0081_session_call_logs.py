"""Add session_call_logs — one row per model/tool call inside a conversation
turn, feeding the admin 会话日志 (session log) console's call overview.

Revision ID: 0081
Revises: 0080
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_call_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("tool_kind", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="completed"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_session_call_logs_conversation_id", "session_call_logs", ["conversation_id"]
    )
    op.create_index(
        "ix_session_call_logs_message_id", "session_call_logs", ["message_id"]
    )
    op.create_index(
        "ix_session_call_logs_created_at", "session_call_logs", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_session_call_logs_created_at", table_name="session_call_logs")
    op.drop_index("ix_session_call_logs_message_id", table_name="session_call_logs")
    op.drop_index("ix_session_call_logs_conversation_id", table_name="session_call_logs")
    op.drop_table("session_call_logs")
