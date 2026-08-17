"""Add content_hash / last_synced_at to agent_skills — loop-free skill sync.

Direction A (DB → FS) writes a content_hash into the SKILL.md frontmatter;
Direction B (FS → DB) skips rows whose FS hash equals the DB hash — the file
is a projection of the current DB version, so the scan can never bounce the
DB back to a stale FS copy after a platform/evolution edit (the A→B→A loop).

Revision ID: 0092
Revises: 0091
Create Date: 2026-08-13
"""
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_skills ADD COLUMN content_hash VARCHAR(64)")
    op.execute("ALTER TABLE agent_skills ADD COLUMN last_synced_at TIMESTAMP WITH TIME ZONE")
    op.execute(
        "CREATE INDEX ix_agent_skills_content_hash ON agent_skills (content_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_skills_content_hash")
    op.execute("ALTER TABLE agent_skills DROP COLUMN IF EXISTS content_hash")
    op.execute("ALTER TABLE agent_skills DROP COLUMN IF EXISTS last_synced_at")
