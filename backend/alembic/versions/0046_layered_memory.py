"""layered memory: memory_episodes (searchable episodic layer) + agent_skills
(triggerable skills layer), both indexed with pg_trgm for CJK-safe retrieval —
see conversation_service.py's ILIKE-over-tsvector rationale for why trigram,
not tsvector, is used here too.

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-05
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "memory_episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "conversation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("raw_excerpt_chars", sa.Integer, nullable=False, server_default="0"),
        sa.Column("consolidated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "CREATE INDEX ix_memory_episodes_summary_trgm ON memory_episodes "
        "USING GIN (summary gin_trgm_ops)"
    )

    op.create_table(
        "agent_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "owner_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True,
        ),
        sa.Column(
            "team_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True,
        ),
        sa.Column(
            "profile_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=True, index=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("trigger_conditions", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "CREATE INDEX ix_agent_skills_description_trgm ON agent_skills "
        "USING GIN (description gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_agent_skills_description_trgm", table_name="agent_skills")
    op.drop_table("agent_skills")
    op.drop_index("ix_memory_episodes_summary_trgm", table_name="memory_episodes")
    op.drop_table("memory_episodes")
