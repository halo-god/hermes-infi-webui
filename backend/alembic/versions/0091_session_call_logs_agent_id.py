"""Add agent_id to session_call_logs — attribute roundtable calls per AI.

Roundtable replies all share the same roundtable message_id, so the admin
session log needs the emitting AI to split calls per reply card. NULL for
personal (single-agent) turns; the runner now fills it for group/roundtable.

Revision ID: 0091
Revises: 0090
Create Date: 2026-08-13
"""
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE session_call_logs ADD COLUMN agent_id VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX ix_session_call_logs_agent ON session_call_logs (agent_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_session_call_logs_agent")
    op.execute("ALTER TABLE session_call_logs DROP COLUMN agent_id")
