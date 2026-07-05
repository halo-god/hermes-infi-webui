"""Self-evolving skills: usage instrumentation + proposal review queue.
`SkillFiring` records which skill fired for which message, feeding the
eval-dataset builder (skill_evolution/dataset.py). `SkillProposal` is the
output of an optimization run (skill_evolution/optimizer.py) — a candidate
content rewrite awaiting super_admin approval before it ever touches the
live AgentSkill row."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class SkillProposal(UUIDPrimaryKey, Timestamps, Base):
    """A candidate content rewrite for one skill, produced by an evolution
    run. Never applied automatically — only a super_admin's approve action
    (skill_evolution API's PATCH endpoint) writes it into AgentSkill.content."""
    __tablename__ = "skill_proposals"
    __table_args__ = (
        Index("ix_skill_proposals_skill_status", "skill_id", "status"),
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposed_content: Mapped[str] = mapped_column(Text, nullable=False)
    # v1 only optimizes .content; column reserved so a later description
    # optimizer doesn't need its own migration.
    proposed_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_score_before: Mapped[float] = mapped_column(Float, nullable=False)
    eval_score_after: Mapped[float] = mapped_column(Float, nullable=False)
    diff_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    dataset_summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
