"""Add preview_pdf_key to workspace_files for unified PDF previews.

All Office uploads (docx/xlsx/pptx/doc/xls/ppt/odt/ods/odp) are converted to
PDF by LibreOffice in the background; the PDF bytes live in object storage
under this key and the workspace panel renders them in the existing pdf
iframe. `storage_key` keeps pointing at the ORIGINAL uploaded bytes (still
used for download and AI raw reads); `content` keeps text extracted from the
PDF for AI injection.

Revision ID: 0086
Revises: 0085
Create Date: 2026-08-11
"""
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace_files ADD COLUMN preview_pdf_key VARCHAR(512)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE workspace_files DROP COLUMN preview_pdf_key")
