"""skill_firings: instrumentation of which skill fired for which message —
feeds the eval-dataset builder for the self-evolving skills pipeline.

Revision ID: 0048
Revises: 0047
Create Date: 2026-07-05
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_firings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "skill_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "conversation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "owner_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("trigger_query_excerpt", sa.Text, nullable=False, server_default=""),
        sa.Column("match_reason", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skill_firings_skill_created", "skill_firings", ["skill_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_skill_firings_skill_created", table_name="skill_firings")
    op.drop_table("skill_firings")
