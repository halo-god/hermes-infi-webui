"""team_tasks board (#9) — Leader-decomposed tasks run by parallel Teammate
subagents. The board belongs to a conversation; each executing task owns one
BackgroundSubagent row, and the runner mirrors subagent lifecycle into task
status (runner_subagent._sync_task_from_subagent).

Revision ID: 0080
Revises: 0079
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_tasks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subagent_id", sa.Uuid(), sa.ForeignKey("background_subagents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assignee_profile_id", sa.Uuid(), sa.ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assignee_name", sa.String(120), server_default="", nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(16), server_default="todo", nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_team_tasks_conversation_id", "team_tasks", ["conversation_id"])
    op.create_index("ix_team_tasks_subagent_id", "team_tasks", ["subagent_id"])


def downgrade() -> None:
    op.drop_index("ix_team_tasks_subagent_id", table_name="team_tasks")
    op.drop_index("ix_team_tasks_conversation_id", table_name="team_tasks")
    op.drop_table("team_tasks")
