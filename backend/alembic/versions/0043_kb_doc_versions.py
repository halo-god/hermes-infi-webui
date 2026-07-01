"""team knowledge + project doc version history

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-01
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_knowledge",
        sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
    )
    op.add_column(
        "project_docs",
        sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
    )

    op.create_table(
        "team_knowledge_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "team_knowledge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("team_knowledge.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version_num", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("author", sa.String(120), nullable=True),
    )
    op.create_index(
        "ix_tkv_item_version", "team_knowledge_versions", ["team_knowledge_id", "version_num"]
    )

    op.create_table(
        "project_doc_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "project_doc_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_docs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version_num", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("author", sa.String(120), nullable=True),
    )
    op.create_index(
        "ix_pdv_item_version", "project_doc_versions", ["project_doc_id", "version_num"]
    )


def downgrade() -> None:
    op.drop_index("ix_pdv_item_version", table_name="project_doc_versions")
    op.drop_table("project_doc_versions")
    op.drop_index("ix_tkv_item_version", table_name="team_knowledge_versions")
    op.drop_table("team_knowledge_versions")
    op.drop_column("project_docs", "current_version")
    op.drop_column("team_knowledge", "current_version")
