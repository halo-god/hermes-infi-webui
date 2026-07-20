"""Add missing database indexes for common query patterns.

Indexes added:
  - messages.owner_id (single + composite with created_at) -- analytics queries
  - group_members.user_id -- reverse lookups (is_member, list_my_groups)
  - team_knowledge.folder_id -- folder tree traversal
  - project_docs.folder_id -- folder tree traversal
  - project_tasks (project_id, order_idx) -- sorted task listing
  - messages.mentions GIN -- JSONB contains queries for unread badges
  - scheduled_tasks (enabled, next_run_at) -- tick() polling query

Revision ID: 0053
Revises: 0052
Create Date: 2026-07-15
"""
from alembic import op
from sqlalchemy import text as sa_text

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    def index_exists(name: str) -> bool:
        return bool(conn.execute(
            sa_text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
            {"name": name},
        ).scalar())

    # Message.owner_id: used by analytics (WHERE owner_id = ?) and memory
    # consolidation queries. Composite with created_at covers the common
    # "per-user, ordered by time" analytics pattern.
    if not index_exists("ix_messages_owner_id"):
        op.create_index("ix_messages_owner_id", "messages", ["owner_id"])
    if not index_exists("ix_messages_owner_created"):
        op.create_index("ix_messages_owner_created", "messages", ["owner_id", "created_at"])

    # GroupMember.user_id: reverse lookups for "which groups am I in?".
    if not index_exists("ix_group_members_user_id"):
        op.create_index("ix_group_members_user_id", "group_members", ["user_id"])

    # Knowledge folder traversal: _collect_folder_knowledge_ids recursively
    # queries WHERE folder_id = ? at each depth.
    if not index_exists("ix_team_knowledge_folder_id"):
        op.create_index("ix_team_knowledge_folder_id", "team_knowledge", ["folder_id"])
    if not index_exists("ix_project_docs_folder_id"):
        op.create_index("ix_project_docs_folder_id", "project_docs", ["folder_id"])

    # Task listing: ORDER BY order_idx within a project.
    if not index_exists("ix_project_tasks_project_order"):
        op.create_index("ix_project_tasks_project_order", "project_tasks", ["project_id", "order_idx"])

    # JSONB mentions: unread_summary uses .contains() on the mentions column.
    if not index_exists("ix_messages_mentions_gin"):
        op.create_index(
            "ix_messages_mentions_gin", "messages", ["mentions"],
            postgresql_using="gin",
        )

    # Scheduled task polling: WHERE enabled AND next_run_at <= now().
    if not index_exists("ix_scheduled_tasks_enabled_nextrun"):
        op.create_index("ix_scheduled_tasks_enabled_nextrun", "scheduled_tasks", ["enabled", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_tasks_enabled_nextrun", table_name="scheduled_tasks")
    op.drop_index("ix_messages_mentions_gin", table_name="messages")
    op.drop_index("ix_project_tasks_project_order", table_name="project_tasks")
    op.drop_index("ix_project_docs_folder_id", table_name="project_docs")
    op.drop_index("ix_team_knowledge_folder_id", table_name="team_knowledge")
    op.drop_index("ix_group_members_user_id", table_name="group_members")
    op.drop_index("ix_messages_owner_created", table_name="messages")
    op.drop_index("ix_messages_owner_id", table_name="messages")
