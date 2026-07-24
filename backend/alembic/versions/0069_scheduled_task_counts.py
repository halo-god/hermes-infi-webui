"""Add success_count + fail_count to scheduled_tasks for execution statistics.

Revision ID: 0069
Revises: 0068
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scheduled_tasks", sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("scheduled_tasks", sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("scheduled_tasks", "fail_count")
    op.drop_column("scheduled_tasks", "success_count")
