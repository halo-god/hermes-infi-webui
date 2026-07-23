"""agent_traces + artifacts: fine-grained cost tracing and executable artifacts.

Revision ID: 0062
Revises: 0061
Create Date: 2026-07-23
"""
from alembic import op
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_traces",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("conversation_id", UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        Column("message_id", UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        Column("profile_id", UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True, index=True),
        Column("step_index", Integer, nullable=False),
        Column("event_type", String(16), nullable=False),
        Column("title", Text, server_default=""),
        Column("tokens_in", Integer, server_default="0", nullable=False),
        Column("tokens_out", Integer, server_default="0", nullable=False),
        Column("duration_ms", Integer, nullable=True),
        Column("cost", Float, server_default="0.0", nullable=False),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )
    op.create_table(
        "artifacts",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("conversation_id", UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        Column("message_id", UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        Column("artifact_type", String(16), nullable=False),
        Column("content", Text, nullable=False),
        Column("status", String(16), server_default="draft", nullable=False),
        Column("result", Text, nullable=True),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("agent_traces")
