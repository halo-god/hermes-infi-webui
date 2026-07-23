"""P2-file: extend RAG chunks to project docs.

team_knowledge_chunks currently only references team_knowledge.id. Project docs
are injected via _build_knowledge_prompt's legacy whole-doc path (truncated to
2000 chars) and never enjoy vector retrieval. This adds a nullable
project_doc_id FK so the same chunk table + embedding pipeline serves both.

Revision ID: 0065
Revises: 0064
Create Date: 2026-07-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_knowledge_chunks",
        sa.Column("project_doc_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chunks_project_doc",
        "team_knowledge_chunks",
        "project_docs",
        ["project_doc_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_team_knowledge_chunks_project_doc",
        "team_knowledge_chunks",
        ["project_doc_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_team_knowledge_chunks_project_doc", table_name="team_knowledge_chunks")
    op.drop_constraint("fk_chunks_project_doc", "team_knowledge_chunks", type_="foreignkey")
    op.drop_column("team_knowledge_chunks", "project_doc_id")
