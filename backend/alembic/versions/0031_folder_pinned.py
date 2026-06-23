"""add pinned column to conversation_folders

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_folders",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("conversation_folders", "pinned")
