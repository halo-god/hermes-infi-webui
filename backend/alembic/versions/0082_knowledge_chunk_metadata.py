"""Add source metadata to team_knowledge_chunks for citation traceability.

P1-1: chunks previously carried only (content, embedding) — no document name,
page or section title. The RAG prompt therefore couldn't cite where a chunk
came from. This adds:
  - source_name: the owning document's name (denormalized, avoids joins at
    prompt-build time)
  - page: optional page number (parser provides it when available; None for
    current extractors)
  - parent_title: the section/heading the chunk belongs to (parent-child
    chunking); None for plain sliding-window chunks

Existing rows are backfilled from their owning tables.

Revision ID: 0082
Revises: 0081
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_knowledge_chunks",
        sa.Column("source_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "team_knowledge_chunks",
        sa.Column("page", sa.Integer(), nullable=True),
    )
    op.add_column(
        "team_knowledge_chunks",
        sa.Column("parent_title", sa.String(255), nullable=True),
    )

    # Backfill source_name from the owning tables (best-effort).
    op.execute(
        """
        UPDATE team_knowledge_chunks c
        SET source_name = k.name
        FROM team_knowledge k
        WHERE c.knowledge_id = k.id AND c.source_name IS NULL
        """
    )
    op.execute(
        """
        UPDATE team_knowledge_chunks c
        SET source_name = d.name
        FROM project_docs d
        WHERE c.project_doc_id = d.id AND c.source_name IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("team_knowledge_chunks", "parent_title")
    op.drop_column("team_knowledge_chunks", "page")
    op.drop_column("team_knowledge_chunks", "source_name")
