"""Add processing_status to workspace_files for async upload conversion.

P1: conversation attachments that need conversion (office/pdf/large) are now
accepted immediately with processing_status='processing'; the extraction runs
in the background and flips the row to 'ready' (or 'error'). The UI shows a
"转换中" badge and refreshes via the SSE file event.

Revision ID: 0084
Revises: 0083
Create Date: 2026-08-10
"""
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace_files "
        "ADD COLUMN processing_status VARCHAR(16) NOT NULL DEFAULT 'ready'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE workspace_files DROP COLUMN processing_status")
