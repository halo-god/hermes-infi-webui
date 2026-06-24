"""Team / membership / governance + project / task persistence."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import governance as gov
from app.db.models.conversation import Conversation
from app.db.models.team import (
    Project,
    ProjectDoc,
    ProjectTask,
    Team,
    TeamKnowledge,
    TeamMember,
)
from app.db.models.user import User


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    mins = int((datetime.now(tz=timezone.utc) - dt).total_seconds() // 60)
    if mins < 1:
        return "刚刚"
    if mins < 60:
        return f"{mins} 分钟前"
    if mins < 1440:
        return f"{mins // 60} 小时前"
    return f"{mins // 1440} 天前"


# ── membership ──
async def get_membership(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamMember | None:
    return await db.get(TeamMember, {"team_id": team_id, "user_id": user_id})


async def require_membership(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[Team, TeamMember]:
    team = await db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="团队不存在")
    member = await get_membership(db, team_id, user_id)
    if member is None:
        raise HTTPException(status_code=403, detail="你不是该团队成员")
    return team, member


async def require_permission(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID, perm_id: str
) -> tuple[Team, TeamMember]:
    team, member = await require_membership(db, team_id, user_id)
    if not gov.can(team.policy, perm_id, member.role):
        raise HTTPException(status_code=403, detail=f"无「{perm_id}」权限")
    return team, member


# ── teams ──
async def list_teams_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[Team]:
    res = await db.execute(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
        .order_by(Team.created_at.desc())
    )
    return list(res.scalars().all())


async def create_team(
    db: AsyncSession, owner: User, *, name: str, handle, tagline, color
) -> Team:
    team = Team(
        name=name,
        handle=handle,
        tagline=tagline,
        color=color or "#b8852a",
        policy=gov.default_policy(),
    )
    db.add(team)
    await db.flush()
    db.add(
        TeamMember(team_id=team.id, user_id=owner.id, role="owner", status="online")
    )
    await db.commit()
    await db.refresh(team)
    return team


async def list_members(db: AsyncSession, team_id: uuid.UUID) -> list[tuple[TeamMember, User]]:
    res = await db.execute(
        select(TeamMember, User)
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.joined_at)
    )
    return list(res.all())


async def add_member(db: AsyncSession, team_id: uuid.UUID, email: str, role: str) -> TeamMember:
    res = await db.execute(select(User).where(User.email == email.lower()))
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在（需先注册账号）")
    existing = await get_membership(db, team_id, user.id)
    if existing:
        raise HTTPException(status_code=409, detail="该用户已是成员")
    member = TeamMember(team_id=team_id, user_id=user.id, role=role)
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


async def update_member_role(
    db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> TeamMember:
    member = await get_membership(db, team_id, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    if member.role == "owner":
        raise HTTPException(status_code=400, detail="不能修改所有者角色")
    member.role = role
    await db.commit()
    await db.refresh(member)
    return member


async def remove_member(db: AsyncSession, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    member = await get_membership(db, team_id, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    if member.role == "owner":
        raise HTTPException(status_code=400, detail="不能移除所有者")
    await db.delete(member)
    await db.commit()


# ── projects ──
async def list_projects(db: AsyncSession, team_id: uuid.UUID) -> list[Project]:
    res = await db.execute(
        select(Project).where(Project.team_id == team_id).order_by(Project.created_at.desc())
    )
    return list(res.scalars().all())


async def get_project(db: AsyncSession, project_id: uuid.UUID) -> Project | None:
    return await db.get(Project, project_id)


async def create_project(db: AsyncSession, team_id: uuid.UUID, data, owner: User | None = None) -> Project:
    proj = Project(
        team_id=team_id,
        name=data.name,
        handle=data.handle,
        color=data.color or "#b8852a",
        icon=data.icon or "sparkle",
        summary=data.summary,
        sections=data.sections or [],
        pinned_profile_ids=data.pinned_profile_ids or [],
        member_ids=[str(owner.id)] if owner else [],
        deadline=data.deadline,
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)
    return proj


# ── tasks ──
async def list_tasks(db: AsyncSession, project_id: uuid.UUID) -> list[ProjectTask]:
    res = await db.execute(
        select(ProjectTask)
        .where(ProjectTask.project_id == project_id)
        .order_by(ProjectTask.order_idx, ProjectTask.created_at)
    )
    return list(res.scalars().all())


async def create_task(db: AsyncSession, project_id: uuid.UUID, data) -> ProjectTask:
    count = len(await list_tasks(db, project_id))
    task = ProjectTask(
        project_id=project_id,
        title=data.title,
        owner_id=data.owner_id,
        agent_id=data.agent_id,
        order_idx=count,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


# ── team shared profiles ──
async def set_shared_profiles(db: AsyncSession, team: Team, profile_ids: list[str]) -> Team:
    team.shared_profile_ids = profile_ids
    await db.commit()
    await db.refresh(team)
    return team


# ── team knowledge ──
async def list_knowledge(db: AsyncSession, team_id: uuid.UUID) -> list[TeamKnowledge]:
    res = await db.execute(
        select(TeamKnowledge)
        .where(TeamKnowledge.team_id == team_id)
        .order_by(TeamKnowledge.created_at.desc())
    )
    return list(res.scalars().all())


async def add_knowledge(db: AsyncSession, team_id: uuid.UUID, data, user: User) -> TeamKnowledge:
    k = TeamKnowledge(
        team_id=team_id,
        name=data.name,
        kind=data.kind,
        size_bytes=data.size_bytes,
        content=data.content,
        uploaded_by=user.id,
        uploaded_by_name=user.name,
    )
    db.add(k)
    await db.commit()
    await db.refresh(k)
    return k


async def delete_knowledge(db: AsyncSession, kid: uuid.UUID) -> None:
    k = await db.get(TeamKnowledge, kid)
    if k:
        await db.delete(k)
        await db.commit()


async def update_knowledge(db: AsyncSession, kid: uuid.UUID, data) -> TeamKnowledge | None:
    k = await db.get(TeamKnowledge, kid)
    if k is None:
        return None
    for f, v in data.model_dump(exclude_unset=True).items():
        setattr(k, f, v)
    await db.commit()
    await db.refresh(k)
    return k


# ── team enrichment (stats / activity / pinned) ──
async def _count(db: AsyncSession, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for w in where:
        stmt = stmt.where(w)
    return int((await db.execute(stmt)).scalar() or 0)


async def team_threads_count(db: AsyncSession, team_id: uuid.UUID) -> int:
    return await _count(db, Conversation, Conversation.team_id == team_id)


async def team_pinned(db: AsyncSession, team_id: uuid.UUID) -> list[Conversation]:
    res = await db.execute(
        select(Conversation)
        .where(Conversation.team_id == team_id, Conversation.pinned.is_(True))
        .order_by(Conversation.updated_at.desc())
        .limit(6)
    )
    return list(res.scalars().all())


async def team_activity(db: AsyncSession, team: Team) -> list[dict]:
    """Synthesize a recent-activity feed from members joined + projects created."""
    items: list[tuple[datetime, dict]] = []
    rows = await list_members(db, team.id)
    for m, u in rows:
        items.append((m.joined_at, {"who": u.name or "成员", "action": "加入了团队",
                                    "target": team.name, "icon": "user", "ago": _ago(m.joined_at)}))
    for p in await list_projects(db, team.id):
        items.append((p.created_at, {"who": "团队", "action": "创建了项目",
                                     "target": p.name, "icon": "cube", "ago": _ago(p.created_at)}))
    for k in await list_knowledge(db, team.id):
        items.append((k.created_at, {"who": k.uploaded_by_name or "成员", "action": "上传了文件",
                                     "target": k.name, "icon": "doc", "ago": _ago(k.created_at)}))
    items.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in items[:8]]


# ── project members / docs / conversations ──
async def set_project_members(db: AsyncSession, project: Project, user_ids: list[str]) -> Project:
    project.member_ids = user_ids
    await db.commit()
    await db.refresh(project)
    return project


async def members_by_ids(db: AsyncSession, team_id: uuid.UUID, user_ids: list[str]):
    rows = await list_members(db, team_id)
    by_id = {str(m.user_id): (m, u) for m, u in rows}
    return [by_id[uid] for uid in user_ids if uid in by_id]


async def list_docs(db: AsyncSession, project_id: uuid.UUID) -> list[ProjectDoc]:
    res = await db.execute(
        select(ProjectDoc).where(ProjectDoc.project_id == project_id).order_by(ProjectDoc.created_at.desc())
    )
    return list(res.scalars().all())


async def add_doc(db: AsyncSession, project_id: uuid.UUID, data, user: User) -> ProjectDoc:
    d = ProjectDoc(
        project_id=project_id, name=data.name, kind=data.kind,
        size_bytes=data.size_bytes, created_by_name=user.name,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


async def delete_doc(db: AsyncSession, did: uuid.UUID) -> None:
    d = await db.get(ProjectDoc, did)
    if d:
        await db.delete(d)
        await db.commit()


async def project_conversations(db: AsyncSession, project_id: uuid.UUID) -> list[Conversation]:
    res = await db.execute(
        select(Conversation)
        .where(Conversation.project_id == project_id)
        .order_by(Conversation.updated_at.desc())
        .limit(20)
    )
    return list(res.scalars().all())
