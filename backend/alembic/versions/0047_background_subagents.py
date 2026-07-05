"""background_subagents: persistent/async ACP subagent orchestration state.

The subagent's transcript lives on a normal (headless) Conversation row —
this table only tracks status/deadlines/read-state, not messages.

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-05
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_subagents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "parent_conversation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "subagent_conversation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, unique=True,
        ),
        sa.Column(
            "owner_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("purpose", sa.Text, nullable=False, server_default=""),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column(
            "profile_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="starting"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_lifetime_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_background_subagents_parent_status",
        "background_subagents", ["parent_conversation_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_background_subagents_parent_status", table_name="background_subagents")
    op.drop_table("background_subagents")
