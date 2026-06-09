"""add is_folder to workspace_files

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_files", sa.Column("is_folder", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("workspace_files", "is_folder")
