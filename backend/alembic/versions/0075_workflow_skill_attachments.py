"""Profile workflow DAG + skill attachments.

`profiles.workflow` stores a visual-workflow DAG (nodes + edges) as JSONB so
users can design multi-agent flows on a canvas instead of toggling MoA/chain/
research checkboxes. The dispatch layer compiles it into an execution plan.

`skill_attachments` lets a SkillTemplate carry auxiliary files (scripts,
examples, docs) alongside its SKILL.md content — aligning with hermes-agent's
native multi-file skill structure.

Revision ID: 0075
Revises: 0074
Create Date: 2026-07-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Profile workflow DAG (nodes + edges JSON).
    op.add_column(
        "profiles",
        sa.Column("workflow", postgresql.JSONB(), nullable=True),
    )

    # Skill attachments — auxiliary files for a SkillTemplate.
    op.create_table(
        "skill_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("template_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("skill_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False, server_default="text/plain"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(32), nullable=False, server_default="doc"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skill_attachments_template", "skill_attachments", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_attachments_template", table_name="skill_attachments")
    op.drop_table("skill_attachments")
    op.drop_column("profiles", "workflow")
