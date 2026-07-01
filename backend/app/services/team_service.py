"""Team / membership / governance + project / task persistence."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import json as _json

from app.core import governance as gov
from app.core import redis as redis_core
from app.db.models.conversation import Conversation
from app.db.models.team import (
    Project,
    ProjectActivity,
    ProjectDoc,
    ProjectDocVersion,
    ProjectTask,
    Team,
    TeamKnowledge,
    TeamKnowledgeVersion,
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
        owner_id=getattr(data, "owner_id", None),
        agent_id=getattr(data, "agent_id", None),
        description=getattr(data, "description", None),
        source_conversation_id=getattr(data, "source_conversation_id", None),
        source_message_id=getattr(data, "source_message_id", None),
        order_idx=count,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


# ── project activity / progress / status (closed-loop core) ──
async def log_activity(
    db: AsyncSession,
    *,
    project: Project,
    actor: User | None,
    kind: str,
    summary: str,
    meta: dict | None = None,
    commit: bool = True,
) -> ProjectActivity:
    """Record one closed-loop action onto the project's activity feed."""
    act = ProjectActivity(
        project_id=project.id,
        team_id=project.team_id,
        actor_id=actor.id if actor else None,
        actor_name=(actor.name if actor else None) or "系统",
        kind=kind,
        summary=summary,
        meta=meta or {},
    )
    db.add(act)
    if commit:
        await db.commit()
        await db.refresh(act)
    return act


async def list_project_activity(
    db: AsyncSession, project_id: uuid.UUID, limit: int = 30
) -> list[ProjectActivity]:
    res = await db.execute(
        select(ProjectActivity)
        .where(ProjectActivity.project_id == project_id)
        .order_by(ProjectActivity.created_at.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


async def recompute_progress(db: AsyncSession, project: Project) -> int:
    """progress = round(done / total * 100). Writes back onto the project."""
    tasks = await list_tasks(db, project.id)
    total = len(tasks)
    done = sum(1 for t in tasks if t.status == "done")
    progress = round(done / total * 100) if total else 0
    project.progress = progress
    return progress


async def notify_project_members(
    db: AsyncSession,
    project: Project,
    *,
    title: str,
    snippet: str,
    actor_id: uuid.UUID | None,
) -> None:
    """Push a `notify` event to every project member except the actor.

    Mirrors conversation_service._publish_user_message's redis pipe pattern so the
    existing /me/stream unread badges light up with zero new plumbing.
    """
    member_ids: list[str] = []
    for x in (project.member_ids or []):
        sx = str(x)
        if sx and sx != str(actor_id):
            member_ids.append(sx)
    if not member_ids:
        return
    r = redis_core.get_redis()
    pipe = r.pipeline()
    for uid in member_ids:
        event = _json.dumps({
            "type": "notify",
            "conversation_id": None,
            "project_id": str(project.id),
            "title": title,
            "snippet": snippet[:80],
            "mention": False,
        })
        pipe.xadd(
            redis_core.user_stream(uid), {"data": event},
            maxlen=redis_core.USER_STREAM_MAXLEN, approximate=True,
        )
        pipe.expire(redis_core.user_stream(uid), redis_core.USER_STREAM_TTL)
    if len(pipe):
        await pipe.execute()


async def move_task_status(
    db: AsyncSession, task: ProjectTask, new_status: str, actor: User | None
) -> ProjectTask:
    """Single entry point for task status changes: status → progress → activity → notify."""
    project = await db.get(Project, task.project_id)
    old_status = task.status
    task.status = new_status
    if project is not None:
        await recompute_progress(db, project)
        kind = "task.done" if new_status == "done" else "task.moved"
        verb = "完成了任务" if new_status == "done" else "推进了任务"
        await log_activity(
            db, project=project, actor=actor, kind=kind,
            summary=f"{verb}「{task.title}」",
            meta={"task_id": str(task.id), "from_status": old_status, "to_status": new_status},
            commit=False,
        )
    await db.commit()
    await db.refresh(task)
    if project is not None:
        await notify_project_members(
            db, project,
            title=project.name,
            snippet=f"任务「{task.title}」→ {new_status}",
            actor_id=actor.id if actor else None,
        )
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


async def delete_knowledge(db: AsyncSession, team_id: uuid.UUID, kid: uuid.UUID) -> None:
    k = await db.get(TeamKnowledge, kid)
    if k and k.team_id == team_id:
        await db.delete(k)
        await db.commit()


_KB_VERSION_CAP = 10  # keep only the latest N historical versions


async def update_knowledge(
    db: AsyncSession, team_id: uuid.UUID, kid: uuid.UUID, data, author: str | None = None
) -> TeamKnowledge | None:
    k = await db.get(TeamKnowledge, kid)
    if k is None or k.team_id != team_id:
        return None
    fields = data.model_dump(exclude_unset=True)
    if "content" in fields and fields["content"] != k.content and k.content:
        ver = TeamKnowledgeVersion(
            team_knowledge_id=k.id, version_num=k.current_version,
            content=k.content, size_bytes=k.size_bytes, author=author,
        )
        db.add(ver)
        old = (await db.execute(
            select(TeamKnowledgeVersion)
            .where(TeamKnowledgeVersion.team_knowledge_id == k.id)
            .order_by(TeamKnowledgeVersion.version_num.desc())
            .offset(_KB_VERSION_CAP)
        )).scalars().all()
        for o in old:
            await db.delete(o)
        k.current_version += 1
    for f, v in fields.items():
        setattr(k, f, v)
    await db.commit()
    await db.refresh(k)
    return k


async def list_knowledge_versions(
    db: AsyncSession, kid: uuid.UUID
) -> list[TeamKnowledgeVersion]:
    res = await db.execute(
        select(TeamKnowledgeVersion)
        .where(TeamKnowledgeVersion.team_knowledge_id == kid)
        .order_by(TeamKnowledgeVersion.version_num.desc())
    )
    return list(res.scalars().all())


async def restore_knowledge_version(
    db: AsyncSession, team_id: uuid.UUID, kid: uuid.UUID, version_num: int,
    author: str | None = None,
) -> TeamKnowledge | None:
    k = await db.get(TeamKnowledge, kid)
    if k is None or k.team_id != team_id:
        return None
    res = await db.execute(
        select(TeamKnowledgeVersion).where(
            TeamKnowledgeVersion.team_knowledge_id == kid,
            TeamKnowledgeVersion.version_num == version_num,
        )
    )
    ver = res.scalar_one_or_none()
    if ver is None:
        return k
    from app.schemas.team import KnowledgeUpdate
    return await update_knowledge(
        db, team_id, kid, KnowledgeUpdate(content=ver.content or ""), author=author
    )


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


_ACTIVITY_ICON: dict[str, str] = {
    "task.created": "plus", "task.derived": "sparkle", "task.moved": "bolt",
    "task.done": "check", "doc.created": "doc", "knowledge.created": "doc",
}


async def team_activity(db: AsyncSession, team: Team) -> list[dict]:
    """Recent-activity feed: real ProjectActivity rows merged with members joined,
    projects created and knowledge uploaded (synthesized for events not yet logged)."""
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
    # Real closed-loop activity (task推进 / 沉淀 / 衍生) logged on project_activity.
    res = await db.execute(
        select(ProjectActivity)
        .where(ProjectActivity.team_id == team.id)
        .order_by(ProjectActivity.created_at.desc())
        .limit(20)
    )
    for a in res.scalars().all():
        items.append((a.created_at, {"who": a.actor_name or "成员", "action": a.summary,
                                     "target": "", "icon": _ACTIVITY_ICON.get(a.kind, "bolt"),
                                     "ago": _ago(a.created_at)}))
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
        size_bytes=data.size_bytes, created_by_name=user.name, created_by=user.id,
        content=getattr(data, "content", None),
        source_conversation_id=getattr(data, "source_conversation_id", None),
        source_message_id=getattr(data, "source_message_id", None),
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


async def get_doc(db: AsyncSession, did: uuid.UUID) -> ProjectDoc | None:
    return await db.get(ProjectDoc, did)


async def update_doc_content(
    db: AsyncSession, project_id: uuid.UUID, did: uuid.UUID, content: str,
    author: str | None = None,
) -> ProjectDoc | None:
    d = await db.get(ProjectDoc, did)
    if d is None or d.project_id != project_id:
        return None
    if d.content and content != d.content:
        ver = ProjectDocVersion(
            project_doc_id=d.id, version_num=d.current_version,
            content=d.content, size_bytes=d.size_bytes, author=author,
        )
        db.add(ver)
        old = (await db.execute(
            select(ProjectDocVersion)
            .where(ProjectDocVersion.project_doc_id == d.id)
            .order_by(ProjectDocVersion.version_num.desc())
            .offset(_KB_VERSION_CAP)
        )).scalars().all()
        for o in old:
            await db.delete(o)
        d.current_version += 1
    d.content = content
    d.size_bytes = len(content.encode("utf-8"))
    await db.commit()
    await db.refresh(d)
    return d


async def list_doc_versions(db: AsyncSession, did: uuid.UUID) -> list[ProjectDocVersion]:
    res = await db.execute(
        select(ProjectDocVersion)
        .where(ProjectDocVersion.project_doc_id == did)
        .order_by(ProjectDocVersion.version_num.desc())
    )
    return list(res.scalars().all())


async def restore_doc_version(
    db: AsyncSession, project_id: uuid.UUID, did: uuid.UUID, version_num: int,
    author: str | None = None,
) -> ProjectDoc | None:
    d = await db.get(ProjectDoc, did)
    if d is None or d.project_id != project_id:
        return None
    res = await db.execute(
        select(ProjectDocVersion).where(
            ProjectDocVersion.project_doc_id == did,
            ProjectDocVersion.version_num == version_num,
        )
    )
    ver = res.scalar_one_or_none()
    if ver is None:
        return d
    return await update_doc_content(db, project_id, did, ver.content or "", author=author)


async def set_profile_knowledge(db: AsyncSession, profile, knowledge_ids: list[str]):
    """Bind team_knowledge / project_docs ids to a Profile for context injection."""
    profile.knowledge_ids = [str(k) for k in (knowledge_ids or [])]
    await db.commit()
    await db.refresh(profile)
    return profile


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
