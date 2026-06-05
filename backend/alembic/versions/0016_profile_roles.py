"""Profile roles: add system_prompt, skills, featured columns.

Revision ID: 0016
Revises: 0015
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("system_prompt", sa.Text(), nullable=True))
    op.add_column("profiles", sa.Column("skills", sa.Text(), nullable=True))
    op.add_column("profiles", sa.Column("featured", sa.Boolean(), nullable=False, server_default="false"))

    # Seed system_prompt + featured flag on the default hermes-main profile
    op.execute(
        """
        UPDATE profiles
        SET featured = true,
            system_prompt = '你是 Hermes，一个通用 AI 助手。请用简洁、友好的中文回答问题。',
            skills = '["通用","问答","分析","写作"]'
        WHERE handle = 'hermes-main'
        """
    )


def downgrade() -> None:
    op.drop_column("profiles", "featured")
    op.drop_column("profiles", "skills")
    op.drop_column("profiles", "system_prompt")
