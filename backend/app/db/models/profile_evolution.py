"""P2-4: profile prompt evolution — usage instrumentation + proposal queue.

Mirrors skill_evolution.py but for Profile.system_prompt. profile_firings feeds
the dataset builder; profile_prompt_proposals holds candidate prompt rewrites
awaiting super_admin approval before touching Profile.system_prompt."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class ProfileFiring(UUIDPrimaryKey, Timestamps, Base):
    """One row per turn where a Profile's system_prompt was active — feeds the
    profile-prompt eval-dataset builder."""
    __tablename__ = "profile_firings"
    __table_args__ = (
        Index("ix_profile_firings_profile_created", "profile_id", "created_at"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger_query_excerpt: Mapped[str] = mapped_column(Text, default="")


class ProfilePromptProposal(UUIDPrimaryKey, Timestamps, Base):
    """A candidate system_prompt rewrite for one Profile, produced by a GEPA
    evolution run. Never applied automatically — only a super_admin's approve
    action writes it into Profile.system_prompt."""
    __tablename__ = "profile_prompt_proposals"
    __table_args__ = (
        Index("ix_profile_proposals_profile_status", "profile_id", "status"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposed_prompt: Mapped[str] = mapped_column(Text, nullable=False)
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
