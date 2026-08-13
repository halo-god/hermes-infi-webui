"""Fix: profile_firings was missing the updated_at column that its ORM model
(Timestamps mixin) expects. The 0063 migration only created created_at.

Kept idempotent: 0063's create_table now ships updated_at too, so a fresh
database would hit a duplicate-column error when replaying 0064.

Revision ID: 0064
Revises: 0063
Create Date: 2026-07-23
"""
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE profile_firings "
        "ADD COLUMN IF NOT EXISTS updated_at "
        "TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE profile_firings DROP COLUMN IF EXISTS updated_at")
