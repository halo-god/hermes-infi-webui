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
from app.db.models.skill_evolution import SkillBranch, SkillProposal, SkillVersion


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


async def _next_version_num(db: AsyncSession, skill_id: uuid.UUID) -> int:
    """Get the next version number for a skill."""
    from sqlalchemy import func
    max_ver = (
        await db.execute(
            select(func.max(SkillVersion.version_num)).where(SkillVersion.skill_id == skill_id)
        )
    ).scalar_one_or_none()
    return (max_ver or 0) + 1


async def review_proposal(
    db: AsyncSession, proposal: SkillProposal, *,
    reviewer_id: uuid.UUID, status: str, review_note: str | None,
) -> SkillProposal:
    """The ONLY path that ever writes an evolution-produced candidate into
    AgentSkill.content. On approval, also creates a SkillVersion snapshot
    for the version history chain.
    """
    proposal.status = status
    proposal.review_note = review_note
    proposal.reviewed_by = reviewer_id
    proposal.reviewed_at = datetime.now(timezone.utc)
    if status == "approved":
        skill = await db.get(AgentSkill, proposal.skill_id)
        if skill is not None:
            skill.content = proposal.proposed_content
            # Create version snapshot for history/rollback.
            ver_num = await _next_version_num(db, proposal.skill_id)
            version = SkillVersion(
                skill_id=proposal.skill_id,
                version_num=ver_num,
                content=proposal.proposed_content,
                description=proposal.proposed_description,
                change_summary=proposal.rationale or "Evolution proposal approved",
                created_by=reviewer_id,
            )
            db.add(version)
    await db.commit()
    await db.refresh(proposal)
    return proposal


async def list_versions(db: AsyncSession, skill_id: uuid.UUID) -> list[SkillVersion]:
    """List all version snapshots of a skill, newest first."""
    rows = (
        await db.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id)
            .order_by(SkillVersion.version_num.desc())
        )
    ).scalars().all()
    return list(rows)


async def rollback_skill(
    db: AsyncSession, skill_id: uuid.UUID, version_num: int,
    user_id: uuid.UUID,
) -> AgentSkill | None:
    """Rollback a skill's content to a historical version. Creates a new
    version snapshot (so the rollback itself is in the history chain)."""
    skill = await db.get(AgentSkill, skill_id)
    if skill is None:
        return None
    target = (
        await db.execute(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill_id,
                SkillVersion.version_num == version_num,
            )
        )
    ).scalars().first()
    if target is None:
        return None
    skill.content = target.content
    new_ver = await _next_version_num(db, skill_id)
    db.add(SkillVersion(
        skill_id=skill_id,
        version_num=new_ver,
        content=target.content,
        description=target.description,
        change_summary=f"Rollback to version {version_num}",
        created_by=user_id,
    ))
    await db.commit()
    await db.refresh(skill)
    return skill


async def get_or_create_branch(
    db: AsyncSession, profile_id: uuid.UUID, skill_id: uuid.UUID,
) -> SkillBranch:
    """Get or lazily create a profile-level branch of a skill."""
    branch = (
        await db.execute(
            select(SkillBranch).where(
                SkillBranch.profile_id == profile_id,
                SkillBranch.skill_id == skill_id,
            )
        )
    ).scalars().first()
    if branch is not None:
        return branch
    skill = await db.get(AgentSkill, skill_id)
    if skill is None:
        raise ValueError("skill not found")
    latest_ver = await _next_version_num(db, skill_id) - 1
    branch = SkillBranch(
        profile_id=profile_id,
        skill_id=skill_id,
        base_version=max(latest_ver, 1),
        head_version=max(latest_ver, 1),
        content=skill.content,
        sync_state="synced",
    )
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    return branch


async def update_branch_content(
    db: AsyncSession, profile_id: uuid.UUID, skill_id: uuid.UUID,
    new_content: str,
) -> SkillBranch | None:
    """Update a profile's branch content, marking it as diverged."""
    branch = await get_or_create_branch(db, profile_id, skill_id)
    branch.content = new_content
    branch.sync_state = "diverged"
    branch.head_version = await _next_version_num(db, skill_id)
    await db.commit()
    await db.refresh(branch)
    return branch


async def sync_branch_from_global(
    db: AsyncSession, profile_id: uuid.UUID, skill_id: uuid.UUID,
) -> SkillBranch | None:
    """Overwrite a profile's branch with the global skill content."""
    skill = await db.get(AgentSkill, skill_id)
    if skill is None:
        return None
    branch = await get_or_create_branch(db, profile_id, skill_id)
    branch.content = skill.content
    branch.sync_state = "synced"
    latest_ver = await _next_version_num(db, skill_id) - 1
    branch.base_version = max(latest_ver, 1)
    branch.head_version = max(latest_ver, 1)
    await db.commit()
    await db.refresh(branch)
    return branch


async def promote_branch_to_global(
    db: AsyncSession, profile_id: uuid.UUID, skill_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AgentSkill | None:
    """Promote a profile's diverged branch back to the global skill,
    creating a new version snapshot."""
    branch = await get_or_create_branch(db, profile_id, skill_id)
    if branch.sync_state != "diverged":
        raise ValueError("branch is not diverged")
    skill = await db.get(AgentSkill, skill_id)
    if skill is None:
        return None
    skill.content = branch.content
    new_ver = await _next_version_num(db, skill_id)
    db.add(SkillVersion(
        skill_id=skill_id,
        version_num=new_ver,
        content=branch.content,
        change_summary=f"Promoted from profile {profile_id}",
        created_by=user_id,
    ))
    branch.sync_state = "synced"
    branch.base_version = new_ver
    branch.head_version = new_ver
    await db.commit()
    await db.refresh(skill)
    return skill
