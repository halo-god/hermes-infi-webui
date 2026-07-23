"""Bridge revision — the production DB was advanced to 0062 by a change whose
migration file is not present in this codebase (likely a sibling feature
branch: knowledge_chunks / sop_* / agent_traces / gallery_items tables exist
in the DB but have no ORM models or code here).

This file is a no-op so alembic's revision graph stays linear (0061 → 0062 →
0063) and `upgrade head` from the current DB state (0062) can proceed. It does
NOT recreate or drop those tables — they are left exactly as-is. If/when the
owning branch merges its own 0062 file, this placeholder should be removed in
favour of the real one.

Revision ID: 0062
Revises: 0061
Create Date: 2026-07-23
"""

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: the schema changes attributed to 0062 were applied out-of-band
    # (the DB is already at this revision). Nothing to do here.
    pass


def downgrade() -> None:
    # Cannot reverse an unknown out-of-band change — leave the schema alone.
    pass
