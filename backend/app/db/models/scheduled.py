"""ScheduledTask model — user-defined recurring ACP agent tasks."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class ScheduledTask(UUIDPrimaryKey, Timestamps, Base):
    """A recurring task that triggers an ACP agent on a cron schedule.

    The FastAPI lifespan starts a 60s polling loop that checks for due tasks
    and enqueues them onto the runner's Redis Stream (same path as chat turns).
    """
    __tablename__ = "scheduled_tasks"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cron: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
