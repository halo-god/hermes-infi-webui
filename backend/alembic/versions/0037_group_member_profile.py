"""add profile_id to group_members + active_profile_ids to conversations

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GroupMember: add profile_id (FK → profiles.id, SET NULL)
    op.add_column(
        "group_members",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_group_members_profile_id",
        "group_members",
        "profiles",
        ["profile_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Conversation: add active_profile_ids (JSONB, for profile-level agent tracking)
    op.add_column(
        "conversations",
        sa.Column(
            "active_profile_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "active_profile_ids")
    op.drop_constraint("fk_group_members_profile_id", "group_members", type_="foreignkey")
    op.drop_column("group_members", "profile_id")
