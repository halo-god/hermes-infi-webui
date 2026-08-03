"""Expert-card fields for profiles (WorkBuddy-style expert management).

Adds avatar/author/category/featured_order/install_count/owner_id/scenarios
so profiles can be presented as rich expert cards with featured curation and
per-user scope visibility.

Revision ID: 0071
Revises: 0070
Create Date: 2026-07-29
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("avatar_url", sa.String(512), nullable=True))
    op.add_column("profiles", sa.Column("author", sa.String(120), nullable=False, server_default=""))
    op.add_column("profiles", sa.Column("category", sa.String(64), nullable=False, server_default=""))
    op.add_column("profiles", sa.Column("featured_order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("profiles", sa.Column("install_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("profiles", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("profiles", sa.Column("scenarios", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.create_index("ix_profiles_featured_order", "profiles", ["featured", "featured_order"])
    op.create_index("ix_profiles_owner_scope", "profiles", ["owner_id", "scope"])


def downgrade() -> None:
    op.drop_index("ix_profiles_owner_scope", table_name="profiles")
    op.drop_index("ix_profiles_featured_order", table_name="profiles")
    for col in ("scenarios", "owner_id", "install_count", "featured_order", "category", "author", "avatar_url"):
        op.drop_column("profiles", col)
