"""Agent memory service — get/upsert per-user memory blocks."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.memory import AgentMemory


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
