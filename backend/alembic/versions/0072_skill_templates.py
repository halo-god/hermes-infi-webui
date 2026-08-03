"""Skill market catalog (SkillTemplate) + agent_skills provenance.

Introduces the `skill_templates` table — the market directory of installable
skills (WorkBuddy SkillHub style) — and adds `agent_skills.template_id` so an
installed skill can be traced back to the template it was materialised from
(used for "update available" detection and install-count aggregation).

Revision ID: 0072
Revises: 0071
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("category", sa.String(64), nullable=False, server_default=""),
        sa.Column("author", sa.String(120), nullable=False, server_default=""),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("source", sa.String(32), nullable=False, server_default="local"),
        sa.Column("featured", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("install_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("icon", sa.String(64), nullable=False, server_default="sparkle"),
        sa.Column("color", sa.String(16), nullable=False, server_default="#b8852a"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now(), nullable=False),
    )
    # name is unique within a source so hermes-FS re-ingest is idempotent.
    op.create_index("ix_skill_templates_name", "skill_templates", ["name"], unique=True)
    op.create_index("ix_skill_templates_category", "skill_templates", ["category"])
    op.create_index("ix_skill_templates_featured", "skill_templates", ["featured"])

    # Provenance: which template an installed AgentSkill was materialised from.
    op.add_column(
        "agent_skills",
        sa.Column("template_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("skill_templates.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_agent_skills_template_id", "agent_skills", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_skills_template_id", table_name="agent_skills")
    op.drop_column("agent_skills", "template_id")
    op.drop_index("ix_skill_templates_featured", table_name="skill_templates")
    op.drop_index("ix_skill_templates_category", table_name="skill_templates")
    op.drop_index("ix_skill_templates_name", table_name="skill_templates")
    op.drop_table("skill_templates")
