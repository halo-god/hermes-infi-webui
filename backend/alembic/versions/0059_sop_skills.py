"""sop_skills + sop_sessions: state machine SOP skill definitions and execution state.

Revision ID: 0059
Revises: 0058
Create Date: 2026-07-23
"""
from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sop_skills",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("profile_id", UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=True, index=True),
        Column("name", String(120), nullable=False),
        Column("description", Text, server_default=""),
        Column("trigger_intents", JSONB, server_default="[]", nullable=False),
        Column("nodes_json", JSONB, server_default="[]", nullable=False),
        Column("edges_json", JSONB, server_default="[]", nullable=False),
        Column("start_node_id", String(64), nullable=False),
        Column("terminal_node_ids", JSONB, server_default="[]", nullable=False),
        Column("enabled", String(8), server_default="true"),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )
    op.create_table(
        "sop_sessions",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("conversation_id", UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        Column("sop_skill_id", UUID(as_uuid=True), ForeignKey("sop_skills.id", ondelete="CASCADE"), nullable=False, index=True),
        Column("current_node_id", String(64), nullable=False),
        Column("slots", JSONB, server_default="{}", nullable=False),
        Column("status", String(16), server_default="active", nullable=False),
        Column("completed_at", DateTime(timezone=True), nullable=True),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sop_sessions")
    op.drop_table("sop_skills")
