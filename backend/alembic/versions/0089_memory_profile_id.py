"""Add profile_id to memory tables — per-profile memory with global fallback.

Each Profile is an independent assistant with its own HERMES_HOME; memory
(user profile / notes / soul / episodic summaries) must follow. Resolution:
profile-scoped row (user_id + profile_id) wins, otherwise fall back to the
global row (user_id + profile_id NULL).

agent_memory drops the plain user_id unique constraint in favour of two
partial unique indexes (Postgres treats NULLs as distinct, so a plain
UNIQUE(user_id, profile_id) would allow unlimited global rows).

Revision ID: 0089
Revises: 0088
Create Date: 2026-08-12
"""
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_memory DROP CONSTRAINT IF EXISTS agent_memory_user_id_key")
    op.execute("ALTER TABLE agent_memory ADD COLUMN profile_id UUID")
    op.execute(
        "CREATE UNIQUE INDEX ix_agent_memory_user_global ON agent_memory (user_id) "
        "WHERE profile_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_agent_memory_user_profile ON agent_memory (user_id, profile_id) "
        "WHERE profile_id IS NOT NULL"
    )
    op.execute("ALTER TABLE memory_episodes ADD COLUMN profile_id UUID")
    op.execute(
        "CREATE INDEX ix_memory_episodes_user_profile ON memory_episodes (user_id, profile_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_memory_user_profile")
    op.execute("DROP INDEX IF EXISTS ix_agent_memory_user_global")
    op.execute("ALTER TABLE agent_memory DROP COLUMN profile_id")
    op.execute("ALTER TABLE memory_episodes DROP COLUMN profile_id")
    op.execute("ALTER TABLE memory_episodes DROP INDEX IF EXISTS ix_memory_episodes_user_profile")
