"""Session call logs — one row per model/tool call inside a conversation turn.

Written by agent_runner at turn finalize time (``_finalize``/``_fail``) so the
admin 会话日志 (session log) console can show a per-call overview with
durations. Model calls are approximated from ``usage`` events (duration =
time since the previous event); tool calls use precise begin/end transitions.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SessionCallLog(Base):
    __tablename__ = "session_call_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Roundtable: all slots share the same roundtable message_id, so calls are
    # attributed per AI via agent_id. NULL for personal (single-agent) turns.
    agent_id: Mapped[str | None] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # model | tool
    name: Mapped[str | None] = mapped_column(String(255))
    tool_kind: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="completed", nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
