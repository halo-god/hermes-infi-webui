"""add invite_token to teams

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("invite_token", sa.String(64), nullable=True))
    op.add_column("teams", sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("teams", sa.Column("invite_role", sa.String(16), nullable=False, server_default="member"))
    op.create_index("ix_teams_invite_token", "teams", ["invite_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_teams_invite_token", table_name="teams")
    op.drop_column("teams", "invite_role")
    op.drop_column("teams", "invite_expires_at")
    op.drop_column("teams", "invite_token")
