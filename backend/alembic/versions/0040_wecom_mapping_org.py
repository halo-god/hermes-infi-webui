"""Add org_id to dept_team_mappings (per-org WeCom mappings)

Revision ID: 0035
Revises: 0039
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dept_team_mappings",
        sa.Column("org_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_dept_team_mappings_org_id", "dept_team_mappings", ["org_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_dept_team_mappings_org_id", table_name="dept_team_mappings")
    op.drop_column("dept_team_mappings", "org_id")
