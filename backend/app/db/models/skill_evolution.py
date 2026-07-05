"""Self-evolving skills: usage instrumentation. `SkillFiring` records which
skill fired for which message, feeding the eval-dataset builder a later
stage uses to drive automated skill-content optimization."""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class SkillFiring(UUIDPrimaryKey, Timestamps, Base):
    """One row per turn where a skill's content was actually injected into
    the system prompt — the instrumentation _build_skills_prompt() itself
    doesn't keep. Feeds the eval-dataset builder (skill_evolution/dataset.py)."""
    __tablename__ = "skill_firings"
    __table_args__ = (
        Index("ix_skill_firings_skill_created", "skill_id", "created_at"),
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    trigger_query_excerpt: Mapped[str] = mapped_column(Text, default="")
    match_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
