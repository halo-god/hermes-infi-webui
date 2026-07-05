"""profiles: is_moa + moa_target_profile_ids — mixture-of-agents as a selectable profile

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("is_moa", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "profiles",
        sa.Column("moa_target_profile_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("profiles", "moa_target_profile_ids")
    op.drop_column("profiles", "is_moa")
