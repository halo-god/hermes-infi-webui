"""team_knowledge + project_docs: storage_key — object storage offload for
large knowledge base / project doc uploads.

Prior to this, TeamKnowledge/ProjectDoc always inlined uploaded content
(base64 for binaries) directly in Postgres regardless of size, unlike
WorkspaceFile which already offloads large/office files to object storage.
This column lets the shared app.core.files.process_upload() helper do the
same for knowledge base and project doc uploads.

Revision ID: 0052
Revises: 0051
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("team_knowledge", sa.Column("storage_key", sa.String(512), nullable=True))
    op.add_column("project_docs", sa.Column("storage_key", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("project_docs", "storage_key")
    op.drop_column("team_knowledge", "storage_key")
