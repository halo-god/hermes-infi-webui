"""Add processing_status to team_knowledge for async Docling processing.

Upload returns immediately with status="processing"; a background task runs
Docling and updates content + status="ready". This avoids upload timeouts on
large documents (2.6MB+ PDFs can take minutes with Docling).

Revision ID: 0067
Revises: 0066
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_knowledge",
        sa.Column("processing_status", sa.String(16), nullable=False, server_default="ready"),
    )


def downgrade() -> None:
    op.drop_column("team_knowledge", "processing_status")
