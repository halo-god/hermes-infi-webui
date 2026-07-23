"""knowledge_chunks: structurally-aware chunking for document RAG.

Revision ID: 0058
Revises: 0057
Create Date: 2026-07-23
"""
from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("team_knowledge_id", UUID(as_uuid=True), ForeignKey("team_knowledge.id", ondelete="CASCADE"), nullable=True, index=True),
        Column("project_doc_id", UUID(as_uuid=True), ForeignKey("project_docs.id", ondelete="CASCADE"), nullable=True, index=True),
        Column("chunk_index", Integer, nullable=False),
        Column("heading_path", Text, nullable=True),
        Column("content", Text, nullable=False),
        Column("summary", Text, nullable=True),
        Column("token_estimate", Integer, nullable=False, server_default="0"),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )
    op.create_index("ix_knowledge_chunks_tk_idx", "knowledge_chunks", ["team_knowledge_id", "chunk_index"])
    op.create_index("ix_knowledge_chunks_pd_idx", "knowledge_chunks", ["project_doc_id", "chunk_index"])
    # pg_trgm GIN index on content for similarity-based chunk retrieval
    op.execute("CREATE INDEX ix_knowledge_chunks_content_trgm ON knowledge_chunks USING gin (content gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_content_trgm")
    op.drop_index("ix_knowledge_chunks_pd_idx", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_tk_idx", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
