"""P2-4: profile prompt evolution — usage instrumentation + proposal queue.

Mirrors skill_evolution's skill_firings + skill_proposals (migrations 0048/0049)
but for Profile.system_prompt optimization. profile_firings records each turn a
profile answered, feeding the dataset builder; profile_prompt_proposals holds
candidate prompt rewrites from a GEPA run, awaiting super_admin approval before
touching the live Profile.system_prompt.

Revision ID: 0063
Revises: 0062
Create Date: 2026-07-23
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_firings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "profile_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "conversation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("trigger_query_excerpt", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_profile_firings_profile_created", "profile_firings", ["profile_id", "created_at"])

    op.create_table(
        "profile_prompt_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "profile_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("proposed_prompt", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("eval_score_before", sa.Float, nullable=False),
        sa.Column("eval_score_after", sa.Float, nullable=False),
        sa.Column("diff_ratio", sa.Float, nullable=False),
        sa.Column("dataset_summary", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "reviewed_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_profile_proposals_profile_status", "profile_prompt_proposals", ["profile_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_profile_proposals_profile_status", table_name="profile_prompt_proposals")
    op.drop_table("profile_prompt_proposals")
    op.drop_index("ix_profile_firings_profile_created", table_name="profile_firings")
    op.drop_table("profile_firings")
