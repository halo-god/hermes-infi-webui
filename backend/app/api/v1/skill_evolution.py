"""Self-evolving skills — admin-only debug/trigger surface. Dataset-building
and (later) optimization logic lives in backend/skill_evolution/, not here;
this file only wires HTTP in and out."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

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
