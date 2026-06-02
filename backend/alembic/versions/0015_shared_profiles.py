"""Add shared_profile_ids to teams."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "teams",
        sa.Column("shared_profile_ids", JSONB, server_default="[]", nullable=False),
    )


def downgrade():
    op.drop_column("teams", "shared_profile_ids")
