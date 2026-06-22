"""branding_assets table (favicon/logo binary upload)

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-22

- Separate table from system_settings.branding (text-only JSON) so the
  whole-document PUT /admin/settings cannot clobber binary bytes.
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "branding_assets",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("mime", sa.String(length=80), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("kind"),
    )


def downgrade() -> None:
    op.drop_table("branding_assets")
