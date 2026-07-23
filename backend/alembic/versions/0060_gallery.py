"""gallery_items: public marketplace table for sharing resources.

Revision ID: 0060
Revises: 0059
Create Date: 2026-07-23
"""
from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gallery_items",
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("type", String(16), nullable=False, index=True),
        Column("item_id", String(64), nullable=False),
        Column("name", String(120), nullable=False),
        Column("description", Text, server_default=""),
        Column("icon", String(40), server_default="sparkle"),
        Column("color", String(16), server_default="#b8852a"),
        Column("category", String(64), nullable=True),
        Column("published_by", UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("published_by_name", String(120), nullable=True),
        Column("published_at", DateTime(timezone=True), nullable=True),
        Column("download_count", Integer, server_default="0", nullable=False),
        Column("snapshot_json", JSONB, server_default="{}", nullable=False),
        Column("created_at", DateTime(timezone=True), server_default="now()", nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default="now()", nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gallery_items")
