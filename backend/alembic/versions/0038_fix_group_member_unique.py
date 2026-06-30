"""fix group_members unique constraint: agent_id → profile_id

Revision ID: 0038
Revises: 0037
Create Date: 2026-06-30

The old unique constraint (conversation_id, agent_id) breaks when multiple
profiles share the same default_agent_id (e.g. 3 profiles all → "hermes").
Replace it with (conversation_id, profile_id) since profile_id is now the
true identity of an agent member.
"""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old agent_id uniqueness constraint
    op.drop_constraint("group_members_unique_agent", "group_members", type_="unique")
    # Add new uniqueness on (conversation_id, profile_id) — allows same agent_id
    # from different profiles. profile_id is nullable for human members.
    op.create_unique_constraint(
        "group_members_unique_profile",
        "group_members",
        ["conversation_id", "profile_id"],
    )


def downgrade() -> None:
    op.drop_constraint("group_members_unique_profile", "group_members", type_="unique")
    op.create_unique_constraint(
        "group_members_unique_agent",
        "group_members",
        ["conversation_id", "agent_id"],
    )
