"""Proposal review queue for self-evolving skills — plain CRUD, no LLM
dependency. The optimization run itself lives in backend/skill_evolution/
(deliberately outside app/services/ — see that package's __init__.py for
why) and agent_runner/runner_skill_evolution.py; this module is only ever
reached from the review endpoints in app/api/v1/skill_evolution.py."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.memory import AgentSkill
from app.db.models.skill_evolution import SkillProposal


async def list_proposals(
    db: AsyncSession, *, status: str | None = None, skill_id: uuid.UUID | None = None,
) -> list[SkillProposal]:
    stmt = select(SkillProposal).order_by(SkillProposal.created_at.desc())
    if status:
        stmt = stmt.where(SkillProposal.status == status)
    if skill_id:
        stmt = stmt.where(SkillProposal.skill_id == skill_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_proposal(db: AsyncSession, proposal_id: uuid.UUID) -> SkillProposal | None:
    return await db.get(SkillProposal, proposal_id)


async def review_proposal(
    db: AsyncSession, proposal: SkillProposal, *,
    reviewer_id: uuid.UUID, status: str, review_note: str | None,
) -> SkillProposal:
    """The ONLY path that ever writes an evolution-produced candidate into
    AgentSkill.content — gated on status == "approved". Stage D's
    optimizer/runner never touch agent_skills directly, only insert
    SkillProposal rows; this is what makes "human review before it takes
    effect" actually true rather than just a comment.

    Both writes commit together in one transaction: if approved, the skill's
    new content and the proposal's reviewed status either land together or
    not at all.
    """
    proposal.status = status
    proposal.review_note = review_note
    proposal.reviewed_by = reviewer_id
    proposal.reviewed_at = datetime.now(timezone.utc)
    if status == "approved":
        skill = await db.get(AgentSkill, proposal.skill_id)
        if skill is not None:
            skill.content = proposal.proposed_content
    await db.commit()
    await db.refresh(proposal)
    return proposal
