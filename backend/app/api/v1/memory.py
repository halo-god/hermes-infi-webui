"""Agent memory endpoints — per-user notes, user_profile, soul + 做梦整理触发."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import redis as redis_core
from app.db.base import get_db
from app.db.models.user import User
from app.deps import get_current_user
from app.services import memory_service

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryOut(BaseModel):
    notes: str | None
    user_profile: str | None
    soul: str | None
    last_consolidated_at: datetime | None = None


class MemoryUpdate(BaseModel):
    notes: str | None = None
    user_profile: str | None = None
    soul: str | None = None


class ConsolidateStatusOut(BaseModel):
    status: str  # idle | running | done | error
    detail: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    cooldown_remaining: int = 0


def _to_out(mem) -> MemoryOut:
    if mem is None:
        return MemoryOut(notes=None, user_profile=None, soul=None)
    return MemoryOut(
        notes=mem.notes,
        user_profile=mem.user_profile,
        soul=mem.soul,
        last_consolidated_at=mem.last_consolidated_at,
    )


@router.get("", response_model=MemoryOut)
async def get_memory(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    mem = await memory_service.get_memory(db, user.id)
    return _to_out(mem)


@router.put("", response_model=MemoryOut)
async def update_memory(
    payload: MemoryUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    # None means "unchanged", so validate against the merged result.
    current = await memory_service.get_memory(db, user.id)
    effective = {
        "notes": payload.notes if payload.notes is not None else (current.notes if current else None),
        "user_profile": payload.user_profile
        if payload.user_profile is not None
        else (current.user_profile if current else None),
        "soul": payload.soul if payload.soul is not None else (current.soul if current else None),
    }
    total = memory_service.memory_total_len(**effective)
    if total > settings.memory_total_chars:
        raise HTTPException(
            status_code=422,
            detail=f"记忆总字数 {total} 超出上限 {settings.memory_total_chars}",
        )
    mem = await memory_service.upsert_memory(
        db,
        user.id,
        notes=payload.notes,
        user_profile=payload.user_profile,
        soul=payload.soul,
    )
    return _to_out(mem)


@router.post("/consolidate", status_code=202)
async def trigger_consolidate(user: User = Depends(get_current_user)) -> dict:
    """手动触发"做梦"记忆整理。普通用户受冷却时间限制，super_admin 不受限（便于测试）。"""
    r = redis_core.get_redis()
    uid = str(user.id)

    if user.role != "super_admin":
        ttl = await r.ttl(redis_core.mem_consolidate_cooldown_key(uid))
        if ttl and ttl > 0:
            raise HTTPException(status_code=429, detail=f"整理记忆冷却中，{ttl} 秒后可再次触发")

    status_key = redis_core.mem_consolidate_status_key(uid)
    status_payload = json.dumps(
        {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False,
    )
    # SET NX doubles as the run lock; a leftover done/error status is stale — overwrite it.
    ok = await r.set(status_key, status_payload, nx=True, ex=settings.memory_consolidate_lock_ttl)
    if not ok:
        existing = await r.get(status_key)
        try:
            if existing and json.loads(existing).get("status") == "running":
                raise HTTPException(status_code=409, detail="整理任务正在进行中")
        except (ValueError, TypeError):
            pass
        await r.set(status_key, status_payload, ex=settings.memory_consolidate_lock_ttl)

    await redis_core.enqueue_prompt({"type": "memory_consolidate", "user_id": uid})

    if user.role != "super_admin":
        await r.set(
            redis_core.mem_consolidate_cooldown_key(uid),
            "1",
            ex=settings.memory_consolidate_cooldown_seconds,
        )
    return {"status": "queued"}


@router.get("/consolidate/status", response_model=ConsolidateStatusOut)
async def consolidate_status(user: User = Depends(get_current_user)) -> ConsolidateStatusOut:
    r = redis_core.get_redis()
    uid = str(user.id)
    raw = await r.get(redis_core.mem_consolidate_status_key(uid))
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = {}
    cooldown = 0
    if user.role != "super_admin":
        ttl = await r.ttl(redis_core.mem_consolidate_cooldown_key(uid))
        cooldown = max(ttl or 0, 0)
    return ConsolidateStatusOut(
        status=data.get("status", "idle"),
        detail=data.get("detail"),
        started_at=data.get("started_at"),
        finished_at=data.get("finished_at"),
        cooldown_remaining=cooldown,
    )
