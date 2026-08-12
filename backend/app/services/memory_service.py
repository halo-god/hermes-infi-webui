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


async def get_memory(
    db: AsyncSession, user_id: uuid.UUID, profile_id: uuid.UUID | None = None,
) -> AgentMemory | None:
    """Resolve the user's memory row.

    Multi-profile: a profile-scoped row (user_id + profile_id) wins; when the
    profile has no memory of its own, fall back to the global row (profile_id
    NULL) so shared facts stay shared.
    """
    if profile_id is not None:
        row = await db.execute(
            select(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.profile_id == profile_id,
            )
        )
        mem = row.scalar_one_or_none()
        if mem is not None:
            return mem
    result = await db.execute(
        select(AgentMemory).where(
            AgentMemory.user_id == user_id,
            AgentMemory.profile_id.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def upsert_memory(
    db: AsyncSession,
    user_id: uuid.UUID,
    notes: str | None = None,
    user_profile: str | None = None,
    soul: str | None = None,
    last_consolidated_at: datetime | None = None,
    profile_id: uuid.UUID | None = None,
) -> AgentMemory:
    # EXACT-scope lookup: a profile-scoped write must create its own row, never
    # mutate the global fallback row (get_memory's fallback is read-only).
    if profile_id is not None:
        row = await db.execute(
            select(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.profile_id == profile_id,
            )
        )
        mem = row.scalar_one_or_none()
    else:
        mem = await get_memory(db, user_id)
    if mem is None:
        mem = AgentMemory(
            user_id=user_id, profile_id=profile_id,
            notes=notes, user_profile=user_profile, soul=soul,
        )
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
    *,
    profile_id: uuid.UUID | None = None,
    commit: bool = True,
) -> MemoryEpisode:
    episode = MemoryEpisode(
        user_id=user_id, profile_id=profile_id,
        conversation_id=conversation_id, title=title[:200],
        summary=summary, raw_excerpt_chars=raw_excerpt_chars, consolidated_at=consolidated_at,
    )
    db.add(episode)
    if commit:
        await db.commit()
        await db.refresh(episode)
    else:
        await db.flush()
    return episode


async def search_episodes(
    db: AsyncSession, user_id: uuid.UUID, query: str, limit: int = 3,
    min_similarity: float = 0.05, profile_id: uuid.UUID | None = None,
) -> list[MemoryEpisode]:
    """pg_trgm similarity search over a user's episode summaries.

    Multi-profile: search the profile's OWN episodes first; when the profile
    has none, fall back to global episodes (shared summaries).

    Trigram (not tsvector) matching, same rationale as the ILIKE title search
    in conversation_service.py: 'simple' config tsvector can't segment CJK
    text, and this product is bilingual/CJK-heavy. Summaries are always
    LLM-condensed (never raw transcript), so retrieval-time injection stays
    bounded regardless of how many episodes match.
    """
    if not query.strip():
        return []
    sim = func.similarity(MemoryEpisode.summary, query)
    # Profile-scoped episodes first; fall back to global (shared) episodes
    # when the profile has none of its own.
    if profile_id is not None:
        profile_stmt = (
            select(MemoryEpisode)
            .where(
                MemoryEpisode.user_id == user_id,
                MemoryEpisode.profile_id == profile_id,
                sim > min_similarity,
            )
            .order_by(sim.desc())
            .limit(limit)
        )
        hits = list((await db.execute(profile_stmt)).scalars().all())
        if hits:
            return hits
    stmt = (
        select(MemoryEpisode)
        .where(
            MemoryEpisode.user_id == user_id,
            MemoryEpisode.profile_id.is_(None),
            sim > min_similarity,
        )
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
    # Sync to hermes filesystem (Direction A).
    from app.services.skill_sync_service import sync_skill_to_hermes
    await sync_skill_to_hermes(
        skill.id, skill.name, skill.description, skill.content,
        skill.enabled, skill.trigger_conditions,
    )
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


async def list_all_skills(db: AsyncSession) -> list[AgentSkill]:
    """Admin-wide view across every owner/team/profile — list_skills() above
    is always scoped, for the personal skill-management page. Backs the
    super_admin skill-evolution review UI, which needs to see every skill."""
    stmt = select(AgentSkill).order_by(AgentSkill.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_skill(db: AsyncSession, skill: AgentSkill, **fields: object) -> AgentSkill:
    """Apply caller-provided fields as-is — pass only explicitly-set fields
    (e.g. via payload.model_dump(exclude_unset=True)) so an intentional
    `None` (like clearing profile_id) isn't silently dropped."""
    for key, value in fields.items():
        setattr(skill, key, value)
    await db.commit()
    await db.refresh(skill)
    # Sync to hermes filesystem (Direction A).
    from app.services.skill_sync_service import sync_skill_to_hermes
    await sync_skill_to_hermes(
        skill.id, skill.name, skill.description, skill.content,
        skill.enabled, skill.trigger_conditions,
    )
    return skill


async def delete_skill(db: AsyncSession, skill: AgentSkill) -> None:
    skill_name = skill.name
    await db.delete(skill)
    await db.commit()
    # Remove from hermes filesystem (Direction A).
    from app.services.skill_sync_service import remove_skill_from_hermes
    await remove_skill_from_hermes(skill_name)
    await db.commit()


async def search_skills(
    db: AsyncSession, *, profile_id: uuid.UUID | None, owner_id: uuid.UUID | None,
    team_id: uuid.UUID | None, query: str, limit: int = 2,
) -> list[AgentSkill]:
    """Return up to `limit` enabled skills bound to this profile/owner/team
    whose trigger matches the incoming message — either an explicit keyword
    hit, an `always` flag, or (as a fallback) high description similarity.

    Multi-profile: the team dimension spans BOTH the profile's own team AND
    every team the user belongs to (a personal-scope profile with team_id=None
    must still trigger team skills), and the profile's OWN skills are ranked
    first so they keep an injection slot instead of being crowded out by
    global always-on skills.
    """
    if not query.strip():
        return []
    scope_clauses = []
    if profile_id:
        scope_clauses.append(AgentSkill.profile_id == profile_id)
    if owner_id:
        scope_clauses.append(AgentSkill.owner_id == owner_id)
    # Team dimension: profile's team ∪ every team the user belongs to.
    team_ids: list[uuid.UUID] = []
    if team_id:
        team_ids.append(team_id)
    if owner_id:
        from app.db.models.team import TeamMember
        rows = (await db.execute(
            select(TeamMember.team_id).where(TeamMember.user_id == owner_id)
        )).scalars().all()
        for tid in rows:
            if tid not in team_ids:
                team_ids.append(tid)
    if team_ids:
        scope_clauses.append(AgentSkill.team_id.in_(team_ids))
    if not scope_clauses:
        return []
    sim = func.similarity(AgentSkill.description, query)
    stmt = (
        select(AgentSkill, sim)
        .where(AgentSkill.enabled.is_(True), or_(*scope_clauses))
        .order_by(sim.desc())
    )
    rows = (await db.execute(stmt)).all()
    # Profile-bound skills rank first — the profile keeps its own slot even
    # when global always-on skills match too.
    rows = sorted(
        rows,
        key=lambda r: (0 if r[0].profile_id == profile_id else 1, -(r[1] or 0)),
    )
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
