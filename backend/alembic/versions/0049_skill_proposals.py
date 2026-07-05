"""skill_proposals: review queue for self-evolving skills — a candidate
content rewrite from an optimization run, awaiting super_admin approval
before it is ever written into agent_skills.

Revision ID: 0049
Revises: 0048
Create Date: 2026-07-05
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "skill_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("proposed_content", sa.Text, nullable=False),
        sa.Column("proposed_description", sa.Text, nullable=True),
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
    op.create_index("ix_skill_proposals_skill_status", "skill_proposals", ["skill_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_skill_proposals_skill_status", table_name="skill_proposals")
    op.drop_table("skill_proposals")
