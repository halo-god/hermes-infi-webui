"""Add origin to agent_skills — distinguish platform vs agent-created skills.

Direction B (FS→DB scan) ingests agent-created skills from the hermes
filesystem; only those rows (origin='agent') should be tombstoned when their
FS directory disappears. Platform skills (admin UI / ZIP import) are the DB
source of truth and must never be silently disabled by a scan.

Revision ID: 0090
Revises: 0089
Create Date: 2026-08-12
"""
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_skills ADD COLUMN origin VARCHAR(16) NOT NULL DEFAULT 'platform'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_skills DROP COLUMN origin")
