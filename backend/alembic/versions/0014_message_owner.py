"""Add owner_id to messages table."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "messages",
        sa.Column("owner_id", UUID(as_uuid=True), nullable=True),
    )


def downgrade():
    op.drop_column("messages", "owner_id")
