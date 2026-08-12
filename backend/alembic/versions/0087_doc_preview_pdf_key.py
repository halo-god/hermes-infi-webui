"""Add preview_pdf_key to project_docs and team_knowledge.

Mirrors workspace_files.preview_pdf_key: LibreOffice-converted PDF bytes in
object storage, served by the new /pdf preview endpoints so knowledge and
project files get the same unified PDF preview as conversation workspace
files (frontend WorkspacePanel `pdf` mode).

Revision ID: 0087
Revises: 0086
Create Date: 2026-08-11
"""
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE project_docs ADD COLUMN preview_pdf_key VARCHAR(512)")
    op.execute("ALTER TABLE team_knowledge ADD COLUMN preview_pdf_key VARCHAR(512)")


def downgrade() -> None:
    op.execute("ALTER TABLE project_docs DROP COLUMN preview_pdf_key")
    op.execute("ALTER TABLE team_knowledge DROP COLUMN preview_pdf_key")
