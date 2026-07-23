"""Fix: profile_firings was missing the updated_at column that its ORM model
(Timestamps mixin) expects. The 0063 migration only created created_at.

Revision ID: 0064
Revises: 0063
Create Date: 2026-07-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_firings",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("profile_firings", "updated_at")
