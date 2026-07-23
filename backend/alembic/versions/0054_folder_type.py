"""conversation_folders: add type column to distinguish personal vs group folders.

Revision ID: 0054
Revises: 0053
Create Date: 2026-07-20
"""
from alembic import op
from sqlalchemy import Column, String, text as sa_text

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Add type column with default 'personal' (all existing folders are personal).
    col_exists = conn.execute(
        sa_text("SELECT 1 FROM information_schema.columns WHERE table_name = 'conversation_folders' AND column_name = 'type'")
    ).scalar()
    if not col_exists:
        op.add_column("conversation_folders",
                      Column("type", String(16), nullable=False, server_default="personal"))

    # Replace the old unique constraint (owner_id, name) with one that includes type,
    # so users can have a "Work" folder for personal chats and another for group chats.
    idx_exists = conn.execute(
        sa_text("SELECT 1 FROM pg_indexes WHERE indexname = 'uq_convo_folder_owner_name_type'")
    ).scalar()
    if not idx_exists:
        # Drop old constraint if it exists.
        old_exists = conn.execute(
            sa_text("SELECT 1 FROM pg_indexes WHERE indexname = 'uq_convo_folder_owner_name'")
        ).scalar()
        if old_exists:
            op.drop_constraint("uq_convo_folder_owner_name", "conversation_folders", type_="unique")
        op.create_unique_constraint(
            "uq_convo_folder_owner_name_type", "conversation_folders", ["owner_id", "name", "type"],
        )


def downgrade() -> None:
    op.drop_constraint("uq_convo_folder_owner_name_type", "conversation_folders", type_="unique")
    op.create_unique_constraint("uq_convo_folder_owner_name", "conversation_folders", ["owner_id", "name"])
    op.drop_column("conversation_folders", "type")
