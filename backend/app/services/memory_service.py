"""Agent memory service — get/upsert per-user memory blocks, plus searchable
episodic memory and a triggerable skills layer."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.memory import AgentMemory, AgentSkill, MemoryEpisode


def memory_total_len(
    notes: str | None, user_profile: str | None, soul: str | None
) -> int:
    """Combined character count of the three memory blocks."""
    return len(notes or "") + len(user_profile or "") + len(soul or "")


async def get_memory(db: AsyncSession, user_id: uuid.UUID) -> AgentMemory | None:
    result = await db.execute(select(AgentMemory).where(AgentMemory.user_id == user_id))
    return result.scalar_one_or_none()


async def upsert_memory(
    db: AsyncSession,
    user_id: uuid.UUID,
    notes: str | None = None,
    user_profile: str | None = None,
    soul: str | None = None,
    last_consolidated_at: datetime | None = None,
) -> AgentMemory:
    mem = await get_memory(db, user_id)
    if mem is None:
        mem = AgentMemory(user_id=user_id, notes=notes, user_profile=user_profile, soul=soul)
        db.add(mem)
    else:
        # Always update when explicitly provided (including empty string to clear)
        if notes is not None:
            mem.notes = notes or None
        if user_profile is not None:
            mem.user_profile = user_profile or None
        if soul is not None:
            mem.soul = soul or None
    if last_consolidated_at is not None:
        mem.last_consolidated_at = last_consolidated_at
    await db.commit()
    await db.refresh(mem)
    return mem


# ── Episodic memory (searchable, one row per consolidated conversation) ────

async def add_episode(
    db: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    title: str,
    summary: str,
    raw_excerpt_chars: int,
    consolidated_at: datetime,
) -> MemoryEpisode:
    episode = MemoryEpisode(
        user_id=user_id, conversation_id=conversation_id, title=title[:200],
        summary=summary, raw_excerpt_chars=raw_excerpt_chars, consolidated_at=consolidated_at,
    )
    db.add(episode)
    await db.commit()
    await db.refresh(episode)
    return episode


async def search_episodes(
    db: AsyncSession, user_id: uuid.UUID, query: str, limit: int = 3, min_similarity: float = 0.05,
) -> list[MemoryEpisode]:
    """pg_trgm similarity search over a user's episode summaries.

    Trigram (not tsvector) matching, same rationale as the ILIKE title search
    in conversation_service.py: 'simple' config tsvector can't segment CJK
    text, and this product is bilingual/CJK-heavy. Summaries are always
    LLM-condensed (never raw transcript), so retrieval-time injection stays
    bounded regardless of how many episodes match.
    """
    if not query.strip():
        return []
    sim = func.similarity(MemoryEpisode.summary, query)
    stmt = (
        select(MemoryEpisode)
        .where(MemoryEpisode.user_id == user_id, sim > min_similarity)
        .order_by(sim.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Skills (procedural memory, injected only when trigger_conditions match) ─

async def create_skill(
    db: AsyncSession, *, name: str, description: str, content: str,
    trigger_conditions: dict | None = None, owner_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None, profile_id: uuid.UUID | None = None,
    enabled: bool = True,
) -> AgentSkill:
    skill = AgentSkill(
        name=name, description=description, content=content,
        trigger_conditions=trigger_conditions or {}, owner_id=owner_id,
        team_id=team_id, profile_id=profile_id, enabled=enabled,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


async def list_skills(
    db: AsyncSession, *, owner_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None, profile_id: uuid.UUID | None = None,
) -> list[AgentSkill]:
    clauses = []
    if owner_id:
        clauses.append(AgentSkill.owner_id == owner_id)
    if team_id:
        clauses.append(AgentSkill.team_id == team_id)
    if profile_id:
        clauses.append(AgentSkill.profile_id == profile_id)
    if not clauses:
        return []
    stmt = select(AgentSkill).where(or_(*clauses)).order_by(AgentSkill.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_skill(db: AsyncSession, skill_id: uuid.UUID) -> AgentSkill | None:
    return await db.get(AgentSkill, skill_id)


async def update_skill(db: AsyncSession, skill: AgentSkill, **fields: object) -> AgentSkill:
    """Apply caller-provided fields as-is — pass only explicitly-set fields
    (e.g. via payload.model_dump(exclude_unset=True)) so an intentional
    `None` (like clearing profile_id) isn't silently dropped."""
    for key, value in fields.items():
        setattr(skill, key, value)
    await db.commit()
    await db.refresh(skill)
    return skill


async def delete_skill(db: AsyncSession, skill: AgentSkill) -> None:
    await db.delete(skill)
    await db.commit()


async def search_skills(
    db: AsyncSession, *, profile_id: uuid.UUID | None, owner_id: uuid.UUID | None,
    team_id: uuid.UUID | None, query: str, limit: int = 2,
) -> list[AgentSkill]:
    """Return up to `limit` enabled skills bound to this profile/owner/team
    whose trigger matches the incoming message — either an explicit keyword
    hit, an `always` flag, or (as a fallback) high description similarity.

    Keyword/always matching is a blunt instrument with no real intent
    understanding, so the caller should keep `limit` small (top-2 default)
    to avoid bloating the prompt with false-positive injections.
    """
    if not query.strip():
        return []
    scope_clauses = []
    if profile_id:
        scope_clauses.append(AgentSkill.profile_id == profile_id)
    if owner_id:
        scope_clauses.append(AgentSkill.owner_id == owner_id)
    if team_id:
        scope_clauses.append(AgentSkill.team_id == team_id)
    if not scope_clauses:
        return []
    sim = func.similarity(AgentSkill.description, query)
    stmt = (
        select(AgentSkill, sim)
        .where(AgentSkill.enabled.is_(True), or_(*scope_clauses))
        .order_by(sim.desc())
    )
    rows = (await db.execute(stmt)).all()
    matched: list[AgentSkill] = []
    for skill, score in rows:
        trig = skill.trigger_conditions or {}
        keywords = [k for k in (trig.get("keywords") or []) if isinstance(k, str) and k]
        hit = bool(trig.get("always")) or any(kw in query for kw in keywords) or (score or 0) > 0.15
        if not hit:
            continue
        matched.append(skill)
        if len(matched) >= limit:
            break
    return matched
