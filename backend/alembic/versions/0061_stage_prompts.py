"""profiles: add stage_prompts for stage-based system prompt injection.

Revision ID: 0061
Revises: 0060
Create Date: 2026-07-23
"""
from alembic import op
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", Column("stage_prompts", JSONB, server_default="{}", nullable=False))


def downgrade() -> None:
    op.drop_column("profiles", "stage_prompts")
