"""employee_work_records: add feedback attribution analysis columns.

Revision ID: 0056
Revises: 0055
Create Date: 2026-07-23
"""
from alembic import op
from sqlalchemy import Column, String, Text

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employee_work_records", Column("analysis_bucket", String(32), nullable=True))
    op.add_column("employee_work_records", Column("analysis_reason", Text, nullable=True))


def downgrade() -> None:
    op.drop_column("employee_work_records", "analysis_reason")
    op.drop_column("employee_work_records", "analysis_bucket")
