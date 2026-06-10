"""Agent memory — per-user free-form memory blocks (notes, user_profile, soul)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class AgentMemory(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "agent_memory"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    user_profile: Mapped[str | None] = mapped_column(Text)
    soul: Mapped[str | None] = mapped_column(Text)
    last_consolidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
