"""BackgroundSubagent — a persistent, non-blocking ACP peer session a parent
conversation spawned. Its transcript lives on a headless `Conversation` row
(type="subagent", excluded from the sidebar) so existing chat-rendering code
displays it with zero changes; this table only tracks orchestration state.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey

# starting -> running -> (idle <-> running) -> done | error | stopped | timeout
# interrupted: runner restarted while this row was still live — see
# reconcile_background_subagents() in agent_runner/runner_subagent.py.
STATUSES = (
    "starting", "running", "idle", "waiting_input",
    "done", "error", "stopped", "timeout", "interrupted",
)


class BackgroundSubagent(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "background_subagents"

    parent_conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subagent_conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(Text, default="")
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="starting", nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idle_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_lifetime_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
