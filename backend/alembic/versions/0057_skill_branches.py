"""skill_versions + skill_branches tables; skill_firings + skill_proposals add columns.

Revision ID: 0057
Revises: 0056
Create Date: 2026-07-23
"""
from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. skill_firings: add content_snapshot
    op.add_column("skill_firings", Column("content_snapshot", Text, nullable=True))

    # 2. skill_proposals: add base_content, parent_version
    op.add_column("skill_proposals", Column("base_content", Text, nullable=True))
    op.add_column("skill_proposals", Column("parent_version", Integer, nullable=True))

    # 3. New table: skill_versions
    op.create_table(
        "skill_versions",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("skill_id", UUID(as_uuid=True), ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True),
        Column("version_num", Integer, nullable=False),
        Column("content", Text, nullable=False),
        Column("description", Text, nullable=True),
        Column("change_summary", Text, nullable=True),
        Column("created_by", UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )
    op.create_index("ix_skill_versions_skill_num", "skill_versions", ["skill_id", "version_num"])

    # 4. New table: skill_branches
    op.create_table(
        "skill_branches",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("profile_id", UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True),
        Column("skill_id", UUID(as_uuid=True), ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True),
        Column("base_version", Integer, nullable=False, server_default="1"),
        Column("head_version", Integer, nullable=False, server_default="1"),
        Column("content", Text, nullable=False),
        Column("sync_state", String(16), nullable=False, server_default="synced"),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )
    op.create_index("ix_skill_branches_profile_skill", "skill_branches", ["profile_id", "skill_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_branches_profile_skill", table_name="skill_branches")
    op.drop_table("skill_branches")
    op.drop_index("ix_skill_versions_skill_num", table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_column("skill_proposals", "parent_version")
    op.drop_column("skill_proposals", "base_content")
    op.drop_column("skill_firings", "content_snapshot")
