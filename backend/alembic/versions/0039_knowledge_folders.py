"""knowledge folders: team_knowledge + project_docs folder hierarchy, profile knowledge_folder_ids

Revision ID: 0039
Revises: 0038
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # team_knowledge: folder hierarchy
    op.add_column("team_knowledge", sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("team_knowledge", sa.Column("is_folder", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("team_knowledge", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.create_foreign_key("fk_team_knowledge_folder", "team_knowledge", "team_knowledge", ["folder_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_team_knowledge_folder_id", "team_knowledge", ["folder_id"])

    # project_docs: folder hierarchy
    op.add_column("project_docs", sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("project_docs", sa.Column("is_folder", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_foreign_key("fk_project_docs_folder", "project_docs", "project_docs", ["folder_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_project_docs_folder_id", "project_docs", ["folder_id"])

    # profiles: knowledge_folder_ids (bind whole directories)
    op.add_column("profiles", sa.Column("knowledge_folder_ids", postgresql.JSONB(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("profiles", "knowledge_folder_ids")
    op.drop_index("ix_project_docs_folder_id", table_name="project_docs")
    op.drop_constraint("fk_project_docs_folder", "project_docs", type_="foreignkey")
    op.drop_column("project_docs", "is_folder")
    op.drop_column("project_docs", "folder_id")
    op.drop_index("ix_team_knowledge_folder_id", table_name="team_knowledge")
    op.drop_constraint("fk_team_knowledge_folder", "team_knowledge", type_="foreignkey")
    op.drop_column("team_knowledge", "sort_order")
    op.drop_column("team_knowledge", "is_folder")
    op.drop_column("team_knowledge", "folder_id")
