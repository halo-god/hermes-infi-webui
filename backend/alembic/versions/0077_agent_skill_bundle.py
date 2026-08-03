"""AgentSkill bundle field for batch-activating skill groups.

Adds a nullable `bundle` column to agent_skills so related skills can be
grouped (e.g. "writing", "coding") and activated/deactivated as a set.

Also adds the skill_sync_service manifest path support (no DB change needed).

Revision ID: 0077
Revises: 0076
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_skills",
        sa.Column("bundle", sa.String(64), nullable=True),
    )
    op.create_index("ix_agent_skills_bundle", "agent_skills", ["bundle"])


def downgrade() -> None:
    op.drop_index("ix_agent_skills_bundle", table_name="agent_skills")
    op.drop_column("agent_skills", "bundle")
