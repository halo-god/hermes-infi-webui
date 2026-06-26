"""Feedback/ticket model — user-submitted feedback with admin reply workflow."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps


class Feedback(Timestamps, Base):
    """A user-submitted feedback ticket (bug report, suggestion, etc.).

    Users create tickets; admins reply and update status/priority.
    Users can see the reply and status of their own tickets.
    """
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    user_name: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(16), default="bug", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(8), default="normal", nullable=False)
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    images: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
