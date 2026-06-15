"""add composite indexes for messages and team_members

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-15
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_msg_conv_created",
        "messages",
        ["conversation_id", "created_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_team_member_user_team",
        "team_members",
        ["user_id", "team_id"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("ix_team_member_user_team", table_name="team_members")
    op.drop_index("ix_msg_conv_created", table_name="messages")
