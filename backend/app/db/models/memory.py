"""Agent memory — per-user free-form memory blocks (notes, user_profile, soul),
plus searchable episodic memory and a triggerable skills layer."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class MemoryEpisode(UUIDPrimaryKey, Timestamps, Base):
    """One per-conversation summary produced by a consolidation run — the
    searchable "episodic" layer. `summary` is always LLM-condensed text, never
    a raw transcript excerpt, so retrieval-time injection stays bounded."""
    __tablename__ = "memory_episodes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    raw_excerpt_chars: Mapped[int] = mapped_column(Integer, default=0)
    consolidated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentSkill(UUIDPrimaryKey, Timestamps, Base):
    """A procedural skill: injected into the system prompt only when
    trigger_conditions match the incoming message, unlike the always-on
    Profile.system_prompt / knowledge bindings."""
    __tablename__ = "agent_skills"

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    trigger_conditions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
