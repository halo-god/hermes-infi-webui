"""Add content_md to workspace_files for background Docling upgrades.

P1: pptx uploads keep the fast python-pptx HTML extraction in `content`
(powering the workspace preview, images inline) while a background Docling
upgrade writes high-quality Markdown (tables/structure) into `content_md`
for AI prompt injection. Preview stays untouched; the AI reads better text.

Revision ID: 0085
Revises: 0084
Create Date: 2026-08-10
"""
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace_files ADD COLUMN content_md TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE workspace_files DROP COLUMN content_md")
