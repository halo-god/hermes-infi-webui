"""Fix: team_knowledge_chunks.knowledge_id must be nullable so project-doc-only
chunks (which set project_doc_id instead) can be inserted. Migration 0065 added
the project_doc_id column but didn't relax knowledge_id's NOT NULL constraint.

Revision ID: 0066
Revises: 0065
Create Date: 2026-07-23
"""
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "team_knowledge_chunks", "knowledge_id",
        nullable=True,
    )


def downgrade() -> None:
    # Re-adding NOT NULL would fail if project-doc-only rows exist; only safe
    # on an empty table. We don't guard here — downgrading past project-doc
    # support is expected to require manual cleanup.
    op.alter_column(
        "team_knowledge_chunks", "knowledge_id",
        nullable=False,
    )
