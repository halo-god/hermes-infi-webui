"""P2-1/P2-2: chain handoff + research mode profile flags.

is_chain + chain_target_profile_ids (P2-1): selecting the profile fans the
message out as a sequential chain — agent A's conclusion is prepended to
agent B's prompt, and so on. Distinct from roundtable (parallel) and MoA
(fan-out + merge). chain_target_profile_ids is ORDERED (the sequence matters).

is_research (P2-2): selecting the profile runs a roundtable in "research mode"
— the first slot to produce a usable answer cancels the rest (cascade
termination) and is returned directly without a merge step.

Revision ID: 0061
Revises: 0060
Create Date: 2026-07-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("is_chain", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "profiles",
        sa.Column("chain_target_profile_ids", sa.dialects.postgresql.JSONB, nullable=False, server_default="[]"),
    )
    op.add_column(
        "profiles",
        sa.Column("is_research", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("profiles", "is_research")
    op.drop_column("profiles", "chain_target_profile_ids")
    op.drop_column("profiles", "is_chain")
