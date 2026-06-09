"""add folder_path to workspace_files

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_files", sa.Column("folder_path", sa.String(512), server_default="/", nullable=False))
    # Update existing standalone files to root
    op.execute("UPDATE workspace_files SET folder_path = '/' WHERE folder_path IS NULL")


def downgrade() -> None:
    op.drop_column("workspace_files", "folder_path")
