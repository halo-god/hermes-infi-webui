"""Self-evolving skills — admin-only debug/trigger surface. Dataset-building
and (later) optimization logic lives in backend/skill_evolution/, not here;
this file only wires HTTP in and out."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import redis as redis_core
from app.core.guards import require_super_admin
from app.db.base import get_db
from app.db.models.user import User
from app.services import memory_service
from skill_evolution.dataset import build_dataset

router = APIRouter(prefix="/skill-evolution", tags=["skill-evolution"])


class DatasetExampleOut(BaseModel):
    query: str
    skill_content_snapshot: str
    output_trace: str | None
    label: str | None
    source: str


class DatasetPreviewOut(BaseModel):
    skill_id: uuid.UUID
    skill_name: str
    examples: list[DatasetExampleOut]
    summary: dict


@router.get("/skills/{skill_id}/preview-dataset", response_model=DatasetPreviewOut)
async def preview_dataset(
    skill_id: uuid.UUID,
    user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db),
) -> DatasetPreviewOut:
    """Read-only: shows what an evolution run would build its eval dataset
    from, without triggering any optimization. No synthetic_generator is
    wired yet (that lands with the DSPy/GEPA optimizer), so a skill with too
    few real firings just previews a smaller, real-only dataset."""
    skill = await memory_service.get_skill(db, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="技能不存在")
    examples, summary = await build_dataset(db, skill)
    return DatasetPreviewOut(
        skill_id=skill.id,
        skill_name=skill.name,
        examples=[DatasetExampleOut(**vars(e)) for e in examples],
        summary=summary.to_dict(),
    )


class EvolveStatusOut(BaseModel):
    status: str  # idle | running | done | error
    detail: str | None = None
    finished_at: str | None = None


@router.post("/skills/{skill_id}/evolve", status_code=202)
async def trigger_evolve(
    skill_id: uuid.UUID,
    user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger an evolution run for one skill. Only ever produces a pending
    SkillProposal (Stage E's approval endpoint is the sole path that can
    change the live skill's content) — this just queues the optimizer."""
    skill = await memory_service.get_skill(db, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="技能不存在")

    r = redis_core.get_redis()
    status_key = redis_core.skill_evolution_status_key(str(skill_id))
    status_payload = json.dumps(
        {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False,
    )
    # SET NX doubles as the run lock, same trick as memory_consolidate.
    ok = await r.set(status_key, status_payload, nx=True, ex=settings.skill_evolution_lock_ttl)
    if not ok:
        existing = await r.get(status_key)
        try:
            if existing and json.loads(existing).get("status") == "running":
                raise HTTPException(status_code=409, detail="该技能已有一次演化正在进行中")
        except (ValueError, TypeError):
            pass
        await r.set(status_key, status_payload, ex=settings.skill_evolution_lock_ttl)

    await redis_core.enqueue_prompt({"type": "skill_evolution", "skill_id": str(skill_id)})
    return {"status": "queued"}


@router.get("/skills/{skill_id}/evolve/status", response_model=EvolveStatusOut)
async def evolve_status(
    skill_id: uuid.UUID,
    user: User = Depends(require_super_admin()),
) -> EvolveStatusOut:
    r = redis_core.get_redis()
    raw = await r.get(redis_core.skill_evolution_status_key(str(skill_id)))
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = {}
    return EvolveStatusOut(
        status=data.get("status", "idle"),
        detail=data.get("detail"),
        finished_at=data.get("finished_at"),
    )
