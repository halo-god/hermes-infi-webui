"""Add monotonic seq to messages for deterministic user-before-agent ordering.

User + agent messages are created in one transaction and therefore share the
same server-side created_at (identical to the microsecond). Ordering by
(created_at, id) then leaves the pair's relative order to random UUIDs, so an
agent reply can sort before its triggering user message (observed live:
agent 086add1c sorted before user a802e88d, same .052286 timestamp).

A conversation-wide monotonic seq — assigned in insertion order via a DB
sequence — gives a stable total order AND a cursor-safe pagination key
(get_messages paginates on (created_at, id) today; mixing role into that key
breaks page boundaries inside a pair, so the key itself must change to seq).

Revision ID: 0093
Revises: 0092
Create Date: 2026-08-18
"""
from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS messages_seq")
    op.execute(
        "ALTER TABLE messages "
        "ADD COLUMN seq BIGINT NOT NULL DEFAULT nextval('messages_seq')"
    )
    # Backfill in DISPLAY order so existing pairs are already user-before-agent
    # and later seq-based queries render identically to the old view.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       ORDER BY created_at,
                                CASE WHEN role = 'user' THEN 0 ELSE 1 END,
                                id
                   ) AS rn
            FROM messages
        )
        UPDATE messages m
        SET seq = r.rn
        FROM ranked r
        WHERE m.id = r.id
        """
    )
    op.execute(
        "SELECT setval('messages_seq', "
        # GREATEST(..., 1): setval rejects 0 (sequence minvalue is 1), which a
        # fresh deployment hits on an empty messages table — without this
        # guard `make migrate` fails on a brand-new database.
        "GREATEST((SELECT COALESCE(MAX(seq), 0) FROM messages), 1), true)"
    )
    op.execute("CREATE INDEX ix_messages_conv_seq ON messages (conversation_id, seq)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_conv_seq")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS seq")
    op.execute("DROP SEQUENCE IF EXISTS messages_seq")
