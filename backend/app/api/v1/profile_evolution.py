"""P2-4: profile prompt evolution — admin-only trigger + review surface.
Mirrors skill_evolution.py. Optimization logic lives in
skill_evolution/profile_optimizer.py; this file only wires HTTP."""
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
from app.db.models.agent import Profile
from app.db.models.user import User
from app.services import profile_evolution_service

router = APIRouter(prefix="/profile-evolution", tags=["profile-evolution"])


def _status_key(profile_id: str) -> str:
    return f"profile_evolution:status:{profile_id}"


class EvolveStatusOut(BaseModel):
    status: str  # idle | running | done | error
    detail: str | None = None
    finished_at: str | None = None


@router.post("/profiles/{profile_id}/evolve", status_code=202)
async def trigger_evolve(
    profile_id: uuid.UUID,
    user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger a profile-prompt evolution run. Only ever produces a pending
    ProfilePromptProposal — approval is the sole path that changes the live
    Profile.system_prompt."""
    profile = await db.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="助手不存在")

    r = redis_core.get_redis()
    status_key = _status_key(str(profile_id))
    status_payload = json.dumps(
        {"status": "running", "started_at": datetime.now(tz=timezone.utc).isoformat()},
        ensure_ascii=False,
    )
    ok = await r.set(status_key, status_payload, nx=True, ex=settings.skill_evolution_lock_ttl)
    if not ok:
        existing = await r.get(status_key)
        try:
            if existing and json.loads(existing).get("status") == "running":
                raise HTTPException(status_code=409, detail="该助手已有一次演化正在进行中")
        except (ValueError, TypeError):
            pass
        await r.set(status_key, status_payload, ex=settings.skill_evolution_lock_ttl)

    await redis_core.enqueue_prompt({"type": "profile_evolution", "profile_id": str(profile_id)})
    return {"status": "queued"}


@router.get("/profiles/{profile_id}/evolve/status", response_model=EvolveStatusOut)
async def evolve_status(
    profile_id: uuid.UUID,
    user: User = Depends(require_super_admin()),
) -> EvolveStatusOut:
    r = redis_core.get_redis()
    raw = await r.get(_status_key(str(profile_id)))
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


# ── Proposal review queue ──

class ProfileProposalOut(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    proposed_prompt: str
    rationale: str | None
    eval_score_before: float
    eval_score_after: float
    diff_ratio: float
    dataset_summary: dict
    status: str
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime


class ProfileProposalReviewIn(BaseModel):
    status: str  # approved | rejected
    review_note: str | None = None


@router.get("/proposals", response_model=list[ProfileProposalOut])
async def list_proposals(
    status: str | None = None,
    profile_id: uuid.UUID | None = None,
    user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db),
) -> list[ProfileProposalOut]:
    proposals = await profile_evolution_service.list_proposals(db, status=status, profile_id=profile_id)
    return [ProfileProposalOut.model_validate(p, from_attributes=True) for p in proposals]


@router.patch("/proposals/{proposal_id}", response_model=ProfileProposalOut)
async def review_proposal(
    proposal_id: uuid.UUID,
    payload: ProfileProposalReviewIn,
    user: User = Depends(require_super_admin()),
    db: AsyncSession = Depends(get_db),
) -> ProfileProposalOut:
    """The only path that can turn a proposal into a live system_prompt change."""
    if payload.status not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="status 必须是 approved 或 rejected")
    proposal = await profile_evolution_service.get_proposal(db, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="提案不存在")
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="该提案已被处理过")
    updated = await profile_evolution_service.review_proposal(
        db, proposal, reviewer_id=user.id, status=payload.status, review_note=payload.review_note,
    )
    return ProfileProposalOut.model_validate(updated, from_attributes=True)
