"""Add tsvector column + GIN index for hybrid (keyword+vector) retrieval.

P1-4: when settings.rag_hybrid is on, the keyword rank comes from a tsvector
match on the chunk content, fused with the pgvector rank via RRF. The column
is a stored generated column (to_tsvector is immutable) so it stays in sync
with content automatically.

Revision ID: 0083
Revises: 0082
Create Date: 2026-08-06
"""
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE team_knowledge_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
        """
    )
    op.execute(
        "CREATE INDEX ix_team_knowledge_chunks_content_tsv "
        "ON team_knowledge_chunks USING GIN (content_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_team_knowledge_chunks_content_tsv")
    op.drop_column("team_knowledge_chunks", "content_tsv")
