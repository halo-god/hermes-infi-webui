"""pgvector RAG: chunk table for semantic retrieval over team knowledge.

Up to now _build_knowledge_prompt() injected whole documents verbatim into the
system prompt (truncated at _KNOWLEDGE_TOTAL=8000 chars). This migration lays
the storage foundation for vector retrieval: each knowledge item is split into
chunks, embedded, and stored here so dispatch can fetch only the top-k most
relevant chunks per turn instead of the whole document.

Uses pgvector (https://github.com/pgvector/pgvector). The embedding model is
BAAI/bge-small-zh-v1.5 → 512 dims. The hnsw index gives sub-linear cosine
search. Kept fully optional: rag_enabled defaults to False, so until an admin
flips it the retrieval path is never taken and this table stays empty.

Revision ID: 0057
Revises: 0056
Create Date: 2026-07-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 512  # BAAI/bge-small-zh-v1.5


def upgrade() -> None:
    # pgvector's Vector type. Imported lazily inside upgrade() so that `alembic
    # history`/`heads` don't fail on envs where pgvector isn't installed yet —
    # only the actual upgrade needs it.
    from pgvector.sqlalchemy import Vector

    # pgvector ships its own type. CREATE EXTENSION needs superuser; if the
    # deployment role lacks it, the migration fails loudly here rather than
    # silently degrading — operators must grant CREATE on the DB or preinstall.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "team_knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column(
            "knowledge_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_knowledge.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        # 512 matches bge-small-zh-v1.5. If the model is later swapped for
        # bge-base (768) the column must be recreated (drop index → alter type
        # → rebuild). Keeping the dimension explicit prevents silently storing
        # mismatched-dim rows.
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Unique pair so re-indexing a doc (delete+insert) can't race into dupes.
    op.create_index(
        "ix_team_knowledge_chunks_knowledge_idx",
        "team_knowledge_chunks",
        ["knowledge_id", "chunk_index"],
        unique=True,
    )
    # HNSW gives sub-linear approximate nearest-neighbour search over cosine
    # distance. vector_cosine_ops is the operator class for <=> (cosine).
    op.execute(
        "CREATE INDEX ix_team_knowledge_chunks_embedding_hnsw "
        "ON team_knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_team_knowledge_chunks_embedding_hnsw")
    op.drop_index("ix_team_knowledge_chunks_knowledge_idx", table_name="team_knowledge_chunks")
    op.drop_table("team_knowledge_chunks")
