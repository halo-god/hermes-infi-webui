"""profiles: add digital employee HR fields + employee_work_records table.

Revision ID: 0055
Revises: 0054
Create Date: 2026-07-21
"""
from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add HR fields to profiles table.
    op.add_column("profiles", Column("employee_no", String(64), nullable=True))
    op.add_column("profiles", Column("department", String(120), nullable=True))
    op.add_column("profiles", Column("position", String(120), nullable=True))
    op.add_column("profiles", Column("employee_status", String(16), server_default="active", nullable=False))
    op.add_column("profiles", Column("hired_at", DateTime(timezone=True), nullable=True))

    # Create employee_work_records table.
    op.create_table(
        "employee_work_records",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("profile_id", UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True),
        Column("conversation_id", UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        Column("message_id", UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        Column("event_type", String(16), nullable=False, server_default="chat"),
        Column("summary", Text, nullable=False, server_default=""),
        Column("tokens_used", Integer, nullable=False, server_default="0"),
        Column("duration_ms", Integer, nullable=True),
        Column("feedback", String(16), nullable=True),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )
    op.create_index("ix_employee_work_records_profile_created", "employee_work_records", ["profile_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_employee_work_records_profile_created", table_name="employee_work_records")
    op.drop_table("employee_work_records")
    op.drop_column("profiles", "hired_at")
    op.drop_column("profiles", "employee_status")
    op.drop_column("profiles", "position")
    op.drop_column("profiles", "department")
    op.drop_column("profiles", "employee_no")
