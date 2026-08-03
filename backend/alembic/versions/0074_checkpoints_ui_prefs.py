"""Workspace checkpoints + per-user UI prefs.

`workspace_checkpoints` records a named snapshot of a conversation's workspace
(the set of files + the version each was at), so a whole turn's file mutations
can be rolled back in one action. `workspace_checkpoint_files` holds zero-copy
pointers into the existing workspace_file_versions chain.

`users.ui_prefs` is a per-user JSONB grab-bag for client-side UI state that must
NOT be injected into the agent context (unlike `preferences`, which is prompt
material). First use: onboarding completion flag. Also repairs the previously
silent no-op where ProfileView PATCHed a non-existent `notify_prefs` field.

Revision ID: 0074
Revises: 0073
Create Date: 2026-07-30
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("author", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_workspace_checkpoints_conversation", "workspace_checkpoints", ["conversation_id"])

    op.create_table(
        "workspace_checkpoint_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("checkpoint_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workspace_checkpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("workspace_files.id", ondelete="CASCADE"), nullable=False),
        # The version this file was at when the checkpoint was taken. A file the
        # checkpoint should NOT contain (created later) is simply absent here;
        # a file deleted before the checkpoint keeps its last known version.
        sa.Column("version_num", sa.Integer(), nullable=False),
        # Snapshot the name too so a since-renamed file restores to its
        # checkpoint-era path.
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("folder_path", sa.String(512), nullable=False, server_default="/"),
    )
    op.create_index("ix_workspace_checkpoint_files_ckpt", "workspace_checkpoint_files", ["checkpoint_id"])

    op.add_column("users", sa.Column("ui_prefs", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "ui_prefs")
    op.drop_index("ix_workspace_checkpoint_files_ckpt", table_name="workspace_checkpoint_files")
    op.drop_table("workspace_checkpoint_files")
    op.drop_index("ix_workspace_checkpoints_conversation", table_name="workspace_checkpoints")
    op.drop_table("workspace_checkpoints")
