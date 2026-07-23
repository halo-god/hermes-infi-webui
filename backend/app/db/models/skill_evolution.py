"""Self-evolving skills: usage instrumentation + proposal review queue + Git-style versioning.

`SkillFiring` records which skill fired for which message, feeding the
eval-dataset builder (skill_evolution/dataset.py). `SkillProposal` is the
output of an optimization run - a candidate content rewrite awaiting approval.

`SkillVersion` and `SkillBranch` add Git-style versioning: each approved
proposal creates a version snapshot, and profiles can have private branches
(synced/diverged) that diverge from the global skill and can be promoted back.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class SkillFiring(UUIDPrimaryKey, Timestamps, Base):
    """One row per turn where a skill's content was actually injected."""
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
    # Snapshot of skill content at firing time, so eval datasets describe
    # the behavior that was actually observed (not the current content).
    content_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)


class SkillVersion(UUIDPrimaryKey, Timestamps, Base):
    """Immutable version snapshot of a skill's content.

    Created when a proposal is approved or when a skill is manually edited.
    Enables rollback to any historical version."""
    __tablename__ = "skill_versions"
    __table_args__ = (
        Index("ix_skill_versions_skill_num", "skill_id", "version_num"),
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SkillBranch(UUIDPrimaryKey, Timestamps, Base):
    """Profile-level private branch of a skill (Git-style).

    sync_state: 'synced' (identical to global) -> 'diverged' (locally modified).
    Can be promoted back to global (creates a new SkillVersion) or synced
    from global (overwrites local changes)."""
    __tablename__ = "skill_branches"
    __table_args__ = (
        Index("ix_skill_branches_profile_skill", "profile_id", "skill_id"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    head_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sync_state: Mapped[str] = mapped_column(String(16), default="synced", nullable=False)  # synced | diverged


class SkillProposal(UUIDPrimaryKey, Timestamps, Base):
    """A candidate content rewrite for one skill, produced by an evolution run."""
    __tablename__ = "skill_proposals"
    __table_args__ = (
        Index("ix_skill_proposals_skill_status", "skill_id", "status"),
    )

    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposed_content: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Base content the proposal was generated from (for diff/version chain).
    base_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
