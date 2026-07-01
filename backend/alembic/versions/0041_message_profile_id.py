"""add profile_id to messages

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Message: add profile_id (FK → profiles.id, SET NULL) so group/roundtable
    # replies can be attributed to the exact Profile that answered, not just
    # the underlying CLI agent_id (ambiguous when profiles share an agent_id).
    op.add_column(
        "messages",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_profile_id",
        "messages",
        "profiles",
        ["profile_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_messages_profile_id", "messages", type_="foreignkey")
    op.drop_column("messages", "profile_id")
