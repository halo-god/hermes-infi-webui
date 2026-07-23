"""P1-3 staged conversations: track each conversation's current stage.

When a profile has staged_enabled=True, this column holds the active stage
key (clarify|implement|review). NULL for non-staged conversations. Changing
it forces an ACP session rebuild (the tool subset may differ per stage).

Revision ID: 0060
Revises: 0059
Create Date: 2026-07-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("staged_stage", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "staged_stage")
