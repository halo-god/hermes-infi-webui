"""Auto-evolution ("Self-improvement review") trigger logic.

After a completed turn the runner calls should_trigger_skill / 
should_trigger_profile to decide whether the skill / profile involved in this
turn qualifies for an automatic evolution run:

- the auto-evolution switch AND the real (LLM) optimizer are both enabled
  (the LLM-free stub must never run — let alone auto-apply — placeholders);
- the entity has accumulated >= evolution_auto_min_firings real firing
  samples since the last proposal;
- the cooldown window (evolution_auto_cooldown_hours) since the last
  proposal has elapsed.

Enqueueing happens on the same Redis Stream as the manual admin triggers, so
the runner workers pick it up with identical routing/retry/DLQ semantics.
All queries are best-effort: a failure here must never block the chat hot
path (the runner wraps the call in try/except).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import redis as redis_core

logger = logging.getLogger(__name__)


def _cooldown_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=settings.evolution_auto_cooldown_hours)


def _auto_eligible() -> bool:
    """Both switches must be on — auto mode never runs the stub optimizer."""
    return bool(settings.evolution_auto_enabled and settings.skill_evolution_enabled)


async def _recent_proposal_exists(
    db: AsyncSession, model, entity_col, entity_id: uuid.UUID, cutoff: datetime,
) -> bool:
    """True when a proposal for this entity was created within the cooldown."""
    row = await db.execute(
        select(func.count())
        .select_from(model)
        .where(
            entity_col == entity_id,
            model.created_at >= cutoff,
        )
    )
    return (row.scalar_one() or 0) > 0


async def _firing_count(db: AsyncSession, model, entity_col, entity_id: uuid.UUID) -> int:
    row = await db.execute(
        select(func.count())
        .select_from(model)
        .where(entity_col == entity_id)
    )
    return row.scalar_one() or 0


async def should_trigger_skill(db: AsyncSession, skill_id: uuid.UUID) -> bool:
    """Decide whether the given skill qualifies for an auto-evolution run."""
    if not _auto_eligible():
        return False
    try:
        from app.db.models.skill_evolution import SkillFiring, SkillProposal
        if await _recent_proposal_exists(
            db, SkillProposal, SkillProposal.skill_id, skill_id, _cooldown_cutoff(),
        ):
            return False  # still inside the cooldown window
        count = await _firing_count(db, SkillFiring, SkillFiring.skill_id, skill_id)
        return count >= settings.evolution_auto_min_firings
    except Exception:  # noqa: BLE001 — never block the chat hot path
        logger.debug("auto-evolution skill check failed", exc_info=True)
        return False


async def should_trigger_profile(db: AsyncSession, profile_id: uuid.UUID) -> bool:
    """Decide whether the given profile qualifies for an auto-evolution run."""
    if not _auto_eligible():
        return False
    try:
        from app.db.models.profile_evolution import (
            ProfileFiring, ProfilePromptProposal,
        )
        if await _recent_proposal_exists(
            db, ProfilePromptProposal, ProfilePromptProposal.profile_id,
            profile_id, _cooldown_cutoff(),
        ):
            return False
        count = await _firing_count(db, ProfileFiring, ProfileFiring.profile_id, profile_id)
        return count >= settings.evolution_auto_min_firings
    except Exception:  # noqa: BLE001
        logger.debug("auto-evolution profile check failed", exc_info=True)
        return False


async def enqueue_skill_evolution(skill_id: uuid.UUID) -> None:
    """Enqueue an evolution run for one skill (same queue as manual trigger)."""
    await redis_core.enqueue_prompt({
        "type": "skill_evolution",
        "skill_id": str(skill_id),
    })
    logger.info("auto-evolution: enqueued skill_evolution for %s", str(skill_id)[:8])


async def enqueue_profile_evolution(profile_id: uuid.UUID) -> None:
    """Enqueue an evolution run for one profile (same queue as manual trigger)."""
    await redis_core.enqueue_prompt({
        "type": "profile_evolution",
        "profile_id": str(profile_id),
    })
    logger.info("auto-evolution: enqueued profile_evolution for %s", str(profile_id)[:8])
