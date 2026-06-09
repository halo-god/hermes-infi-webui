"""Workspace files — AI-produced artifacts attached to a conversation.

P2 stores text content inline in PG for testability; the column maps cleanly
to a MinIO/S3 storage_key in a later step (storage abstraction in agent_runner).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class WorkspaceFile(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "workspace_files"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    folder_path: Mapped[str] = mapped_column(String(512), default="/")  # e.g. "/docs" or "/" for root
    kind: Mapped[str] = mapped_column(String(16), default="md")  # md|docx|csv|json|txt
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str | None] = mapped_column(Text)  # P2: inline; later → storage_key
    storage_key: Mapped[str | None] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by_agent: Mapped[str | None] = mapped_column(String(64))


class WorkspaceFileVersion(UUIDPrimaryKey, Base):
    __tablename__ = "workspace_file_versions"

    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_files.id", ondelete="CASCADE"),
        index=True,
    )
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    author: Mapped[str | None] = mapped_column(String(64))
