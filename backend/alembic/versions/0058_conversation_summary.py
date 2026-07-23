"""P1-2 conversation summaries: cached LLM summary of a conversation's early
history, injected into the prompt prefix so long conversations don't blow the
context window.

One row per conversation (upserted by the summary worker). `covered_up_to_msg_id`
marks how far the summary has progressed; the next summary run picks up from
there. `summary` is always LLM-condensed, never raw transcript.

Revision ID: 0058
Revises: 0057
Create Date: 2026-07-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column(
            "conversation_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
        ),
        sa.Column("summary", sa.Text, nullable=False),
        # The newest message id covered by this summary. Next run resumes here.
        sa.Column("covered_up_to_msg_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("covered_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("token_estimate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversation_summaries")
