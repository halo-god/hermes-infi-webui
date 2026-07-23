"""P1-3 staged system prompts: per-stage prompt + tool subset for profiles.

A profile with staged_enabled=True switches its system_prompt and MCP tool
subset based on the conversation's current stage (clarify → implement →
review), letting one assistant play different roles across a task lifecycle.
This migration adds the per-profile configuration; the per-conversation
"current stage" state lives in 0060.

staged_prompts JSONB shape:
  {
    "clarify":   {"prompt": "...", "mcp_servers": ["read_file"]},
    "implement": {"prompt": "...", "mcp_servers": ["read_file","write_file"]},
    "review":    {"prompt": "...", "mcp_servers": ["read_file"]}
  }
When a stage is absent it inherits profile.system_prompt / full mcp_server_names.

Revision ID: 0059
Revises: 0058
Create Date: 2026-07-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("staged_prompts", sa.dialects.postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "profiles",
        sa.Column("staged_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("profiles", "staged_enabled")
    op.drop_column("profiles", "staged_prompts")
