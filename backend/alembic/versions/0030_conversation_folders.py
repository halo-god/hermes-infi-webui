"""conversation_folders table + conversations.folder_id

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-22

- New table `conversation_folders` for user-defined conversation grouping.
- New nullable `folder_id` FK on `conversations` (ON DELETE SET NULL).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_convo_folder_owner_name"),
    )
    op.create_index(
        "ix_conversation_folders_owner_id", "conversation_folders", ["owner_id"]
    )

    op.add_column(
        "conversations",
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_folder_id",
        "conversations",
        "conversation_folders",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversations_folder_id", "conversations", ["folder_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_folder_id", table_name="conversations")
    op.drop_constraint("fk_conversations_folder_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "folder_id")
    op.drop_index("ix_conversation_folders_owner_id", table_name="conversation_folders")
    op.drop_table("conversation_folders")
