"""add group chat support — type, GroupMember, mentions

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018"
down_revision = "0017"


def upgrade() -> None:
    # 1. Conversation: add type field
    op.add_column(
        "conversations",
        sa.Column("type", sa.String(16), nullable=False, server_default="personal"),
    )
    # Migrate existing is_channel=true to type="group"
    op.execute("UPDATE conversations SET type = 'group' WHERE is_channel = true")

    # 2. Message: add mentions field
    op.add_column("messages", sa.Column("mentions", JSONB, nullable=True))

    # 3. Create group_members table
    op.create_table(
        "group_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("agent_id", sa.String(64), nullable=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # At least one of user_id or agent_id must be set
        sa.CheckConstraint(
            "user_id IS NOT NULL OR agent_id IS NOT NULL",
            name="group_members_user_or_agent",
        ),
    )
    # Unique constraints (partial — NULL doesn't violate unique in PG)
    op.create_unique_constraint(
        "group_members_unique_user", "group_members", ["conversation_id", "user_id"]
    )
    op.create_unique_constraint(
        "group_members_unique_agent", "group_members", ["conversation_id", "agent_id"]
    )

    # 4. Seed group_members from existing conversations
    # Admin = conversation owner
    op.execute("""
        INSERT INTO group_members (id, conversation_id, user_id, role, created_at, updated_at)
        SELECT gen_random_uuid(), id, owner_id, 'admin', now(), now()
        FROM conversations WHERE type = 'group'
    """)
    # Agent members from active_agent_ids (JSONB array)
    op.execute("""
        INSERT INTO group_members (id, conversation_id, agent_id, role, created_at, updated_at)
        SELECT gen_random_uuid(), c.id, jsonb_array_elements_text(c.active_agent_ids), 'member', now(), now()
        FROM conversations c WHERE c.type = 'group' AND c.active_agent_ids IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_table("group_members")
    op.drop_column("messages", "mentions")
    op.drop_column("conversations", "type")
