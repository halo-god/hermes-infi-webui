"""unify team channels into group model + message reply/edit/recall/reactions

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-17

- Backfill legacy is_channel conversations into type='group' and seed their
  GroupMember rows (team roster as members; owner stays admin).
- Enforce one canonical group per team (is_channel) and per project.
- Add Message columns for reply quoting, edit/recall, and JSONB reactions.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Normalize legacy team channels to the unified group model.
    op.execute(
        "UPDATE conversations SET type='group', visibility='team' "
        "WHERE is_channel = true AND type <> 'group'"
    )

    # 2. Seed GroupMember rows for team members of each channel group
    #    (0018 only seeded owner + agents). Skip duplicates via NOT EXISTS.
    op.execute("""
        INSERT INTO group_members (id, conversation_id, user_id, role, joined_at, created_at, updated_at)
        SELECT gen_random_uuid(), c.id, tm.user_id, 'member', now(), now(), now()
        FROM conversations c
        JOIN team_members tm ON tm.team_id = c.team_id
        WHERE c.is_channel = true
          AND NOT EXISTS (
            SELECT 1 FROM group_members gm
            WHERE gm.conversation_id = c.id AND gm.user_id = tm.user_id
          )
    """)

    # 3. One canonical group per team (the is_channel one) and per project.
    op.create_index(
        "uq_group_per_team",
        "conversations",
        ["team_id"],
        unique=True,
        postgresql_where=sa.text("type = 'group' AND is_channel = true"),
    )
    op.create_index(
        "uq_group_per_project",
        "conversations",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("type = 'group' AND project_id IS NOT NULL"),
    )

    # 4. Unread-query index.
    op.create_index(
        "ix_groupmember_user_conv",
        "group_members",
        ["user_id", "conversation_id"],
        postgresql_using="btree",
    )

    # 5. Message enhancements: reply / edit / recall / reactions.
    op.add_column(
        "messages",
        sa.Column(
            "reply_to_id",
            UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("messages", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "messages",
        sa.Column("reactions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("messages", "reactions")
    op.drop_column("messages", "deleted_at")
    op.drop_column("messages", "edited_at")
    op.drop_column("messages", "reply_to_id")
    op.drop_index("ix_groupmember_user_conv", table_name="group_members")
    op.drop_index("uq_group_per_project", table_name="conversations")
    op.drop_index("uq_group_per_team", table_name="conversations")
    # Channel→group normalization is intentionally not reverted (lossless to keep).
