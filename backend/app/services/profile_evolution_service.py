"""P2-4: proposal review queue for profile prompt evolution — plain CRUD, no
LLM. Mirrors skill_evolution_service. The optimization run lives in
skill_evolution/profile_optimizer.py + agent_runner/runner_profile_evolution.py;
this module is only reached from the review endpoints in
app/api/v1/profile_evolution.py."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Profile
from app.db.models.profile_evolution import ProfilePromptProposal


async def list_proposals(
    db: AsyncSession, *, status: str | None = None, profile_id: uuid.UUID | None = None,
) -> list[ProfilePromptProposal]:
    stmt = select(ProfilePromptProposal).order_by(ProfilePromptProposal.created_at.desc())
    if status:
        stmt = stmt.where(ProfilePromptProposal.status == status)
    if profile_id:
        stmt = stmt.where(ProfilePromptProposal.profile_id == profile_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_proposal(db: AsyncSession, proposal_id: uuid.UUID) -> ProfilePromptProposal | None:
    return await db.get(ProfilePromptProposal, proposal_id)


async def review_proposal(
    db: AsyncSession, proposal: ProfilePromptProposal, *,
    reviewer_id: uuid.UUID, status: str, review_note: str | None,
) -> ProfilePromptProposal:
    """The ONLY path that writes an evolution-produced candidate into
    Profile.system_prompt — gated on status == "approved". Both writes commit
    together so the new prompt and the reviewed status land atomically."""
    proposal.status = status
    proposal.review_note = review_note
    proposal.reviewed_by = reviewer_id
    proposal.reviewed_at = datetime.now(timezone.utc)
    if status == "approved":
        profile = await db.get(Profile, proposal.profile_id)
        if profile is not None:
            profile.system_prompt = proposal.proposed_prompt
    await db.commit()
    await db.refresh(proposal)
    return proposal
