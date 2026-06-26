"""project loop: activity log, source tracing, doc content, profile knowledge

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # project_tasks: source tracing + description
    op.add_column("project_tasks", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "project_tasks",
        sa.Column("source_conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "project_tasks",
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # project_docs: content body + creator + source tracing
    op.add_column("project_docs", sa.Column("content", sa.Text(), nullable=True))
    op.add_column(
        "project_docs",
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "project_docs",
        sa.Column("source_conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "project_docs",
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # team_knowledge: source tracing
    op.add_column(
        "team_knowledge",
        sa.Column("source_conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "team_knowledge",
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # profiles: bound knowledge ids
    op.add_column(
        "profiles",
        sa.Column(
            "knowledge_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )

    # project_activity table
    op.create_table(
        "project_activity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_name", sa.String(length=120), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_activity_project", "project_activity", ["project_id"])
    op.create_index(
        "ix_project_activity_team_created", "project_activity", ["team_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_activity_team_created", table_name="project_activity")
    op.drop_index("ix_project_activity_project", table_name="project_activity")
    op.drop_table("project_activity")

    op.drop_column("profiles", "knowledge_ids")

    op.drop_column("team_knowledge", "source_message_id")
    op.drop_column("team_knowledge", "source_conversation_id")

    op.drop_column("project_docs", "source_message_id")
    op.drop_column("project_docs", "source_conversation_id")
    op.drop_column("project_docs", "created_by")
    op.drop_column("project_docs", "content")

    op.drop_column("project_tasks", "source_message_id")
    op.drop_column("project_tasks", "source_conversation_id")
    op.drop_column("project_tasks", "description")
