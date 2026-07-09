"""Teams, members, governance policy, projects, tasks.

Reads require team membership; writes are gated by the team content-permission
matrix (app/core/governance.py). Owner always passes; policy is editable by
owner/admin.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File as FastApiFile, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel as _BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import governance as gov
from app.core.files import read_upload_capped
from app.core.rbac import require_admin
from app.db.base import get_db
from app.db.models.user import User
from app.deps import get_current_user
from app.schemas.team import (
    ActivityItem,
    ActivityOut,
    AddMemberRequest,
    ConversationBrief,
    DocContentUpdate,
    DocCreate,
    DocOut,
    DocVersionOut,
    TaskFromConversation,
    TaskStatusUpdate,
    KnowledgeCreate,
    KnowledgeDetail,
    KnowledgeUpdate,
    KnowledgeOut,
    KnowledgeVersionOut,
    MemberOut,
    PolicyOut,
    PolicyUpdate,
    ProjectCreate,
    ProjectDetail,
    ProjectMembersUpdate,
    ProjectOut,
    ProjectUpdate,
    SharedProfilesUpdate,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    TeamCreate,
    TeamDetail,
    TeamOut,
    TeamStats,
    TeamUpdate,
    UpdateMemberRequest,
)
from app.services import team_service as svc

router = APIRouter()


# ── teams ──
@router.get("/teams", response_model=list[TeamOut])
async def list_teams(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await svc.list_teams_for_user(db, user.id)


@router.post("/teams", response_model=TeamDetail, status_code=201)
async def create_team(
    payload: TeamCreate,
    user: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    team = await svc.create_team(
        db, user, name=payload.name, handle=payload.handle,
        tagline=payload.tagline, color=payload.color,
    )
    return await _team_detail(db, team.id, user)


@router.get("/teams/{team_id}", response_model=TeamDetail)
async def get_team(
    team_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    await svc.require_membership(db, team_id, user.id)
    return await _team_detail(db, team_id, user)


@router.patch("/teams/{team_id}", response_model=TeamOut)
async def update_team(
    team_id: uuid.UUID, payload: TeamUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    team, member = await svc.require_membership(db, team_id, user.id)
    if member.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="仅所有者/管理员可修改团队")
    for f, v in payload.model_dump(exclude_unset=True).items():
        setattr(team, f, v)
    await db.commit()
    await db.refresh(team)
    return team


@router.delete("/teams/{team_id}", status_code=204)
async def delete_team(
    team_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    team, member = await svc.require_membership(db, team_id, user.id)
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="仅所有者可解散团队")
    await db.delete(team)
    await db.commit()


# ── team channel ──
class ChannelModeUpdate(_BaseModel):
    channel_mode: str  # "off" | "mention" | "always"


@router.get("/teams/{team_id}/channel")
async def get_team_channel(
    team_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Return (or create) the team's canonical fixed group chat.

    Unified onto the type="group" + GroupMember model: membership stays synced
    with the team roster + shared agents on every fetch.
    """
    from app.services import conversation_service as conv_svc
    from app.schemas.conversation import ConversationOut
    team, _ = await svc.require_membership(db, team_id, user.id)
    channel = await conv_svc.get_or_create_team_group(db, team, user.id)
    return {
        "channel": ConversationOut.model_validate(channel),
        "channel_mode": channel.channel_mode or team.channel_mode,
    }


@router.get("/projects/{project_id}/group")
async def get_project_group(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Return (or create) the project's canonical fixed group chat."""
    from app.services import conversation_service as conv_svc
    from app.schemas.conversation import ConversationOut
    project = await svc.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await svc.require_membership(db, project.team_id, user.id)
    group = await conv_svc.get_or_create_project_group(db, project, user.id)
    return {
        "channel": ConversationOut.model_validate(group),
        "channel_mode": group.channel_mode,
    }


@router.patch("/teams/{team_id}/channel/mode")
async def set_channel_mode(
    team_id: uuid.UUID, payload: ChannelModeUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    from app.services import conversation_service as conv_svc
    team, member = await svc.require_membership(db, team_id, user.id)
    if member.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="仅所有者/管理员可修改频道设置")
    if payload.channel_mode not in ("off", "mention", "always"):
        raise HTTPException(status_code=422, detail="channel_mode 无效")
    team.channel_mode = payload.channel_mode
    # Mirror onto the canonical group conversation (the live source of truth).
    group = await conv_svc.get_or_create_team_group(db, team, user.id)
    group.channel_mode = payload.channel_mode
    await db.commit()
    return {"channel_mode": team.channel_mode}


@router.delete("/teams/{team_id}/channel/messages", status_code=204)
async def clear_channel_messages(
    team_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Clear all messages in the team channel. Requires channel.clear permission."""
    from app.db.models.conversation import Message
    from app.services import conversation_service as conv_svc
    from sqlalchemy import delete as sa_delete
    team, member = await svc.require_membership(db, team_id, user.id)
    if not gov.can(team.policy, "channel.clear", member.role):
        raise HTTPException(status_code=403, detail="无权清空频道记录")
    channel = await conv_svc.get_or_create_team_group(db, team, user.id)
    if channel:
        await db.execute(sa_delete(Message).where(Message.conversation_id == channel.id))
        await db.commit()


# ── invite token ──
class InviteTokenRequest(_BaseModel):
    role: str = "member"
    expires_days: int = 7  # 0 = never


class InviteTokenOut(_BaseModel):
    token: str
    url: str
    role: str
    expires_days: int


@router.post("/teams/{team_id}/invite-token", response_model=InviteTokenOut)
async def generate_invite_token(
    team_id: uuid.UUID, payload: InviteTokenRequest,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """(Re)generate a shareable invite token. Owner/admin only."""
    import secrets
    from datetime import timezone, timedelta
    team, member = await svc.require_membership(db, team_id, user.id)
    if member.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="仅所有者/管理员可生成邀请链接")
    team.invite_token = secrets.token_urlsafe(32)
    team.invite_role = payload.role
    team.invite_expires_at = (
        None if payload.expires_days == 0
        else datetime.now(timezone.utc) + timedelta(days=payload.expires_days)
    )
    await db.commit()
    await db.refresh(team)
    handle = team.handle or str(team.id)
    return InviteTokenOut(
        token=team.invite_token,
        url=f"/i/{handle}/{team.invite_token}",
        role=team.invite_role,
        expires_days=payload.expires_days,
    )


class JoinRequest(_BaseModel):
    token: str


@router.post("/teams/join-by-token", response_model=dict)
async def join_by_token(
    payload: JoinRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Join a team by presenting a valid invite token."""
    from datetime import timezone
    from app.db.models.team import Team, TeamMember

    res = await db.execute(select(Team).where(Team.invite_token == payload.token))
    team = res.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="邀请链接无效或已失效")
    if team.invite_expires_at and team.invite_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="邀请链接已过期")

    existing = await svc.get_membership(db, team.id, user.id)
    if existing:
        return {"team_id": str(team.id), "role": existing.role, "joined": False, "message": "已是团队成员"}

    member = TeamMember(team_id=team.id, user_id=user.id, role=team.invite_role)
    db.add(member)
    await db.commit()
    return {"team_id": str(team.id), "role": team.invite_role, "joined": True, "message": "加入成功"}


# ── members ──
@router.get("/teams/{team_id}/members", response_model=list[MemberOut])
async def list_members(
    team_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    await svc.require_membership(db, team_id, user.id)
    rows = await svc.list_members(db, team_id)
    return [_member_out(m, u) for m, u in rows]


@router.post("/teams/{team_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    team_id: uuid.UUID, payload: AddMemberRequest,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await svc.require_permission(db, team_id, user.id, "member.invite")
    member = await svc.add_member(db, team_id, payload.email, payload.role)
    target = await db.get(User, member.user_id)
    return _member_out(member, target)


@router.patch("/teams/{team_id}/members/{member_id}", response_model=MemberOut)
async def update_member(
    team_id: uuid.UUID, member_id: uuid.UUID, payload: UpdateMemberRequest,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await svc.require_permission(db, team_id, user.id, "member.role")
    member = await svc.update_member_role(db, team_id, member_id, payload.role)
    target = await db.get(User, member.user_id)
    return _member_out(member, target)


@router.delete("/teams/{team_id}/members/{member_id}", status_code=204)
async def remove_member(
    team_id: uuid.UUID, member_id: uuid.UUID,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await svc.require_permission(db, team_id, user.id, "member.remove")
    await svc.remove_member(db, team_id, member_id)


# ── governance policy ──
@router.get("/teams/{team_id}/policy", response_model=PolicyOut)
async def get_policy(
    team_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    team, member = await svc.require_membership(db, team_id, user.id)
    return PolicyOut(
        my_role=member.role,
        editable=member.role in ("owner", "admin"),
        permissions=gov.grouped_permissions(),
        policy=gov.ensure_policy(team.policy),
    )


@router.put("/teams/{team_id}/policy", response_model=PolicyOut)
async def put_policy(
    team_id: uuid.UUID, payload: PolicyUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    team, member = await svc.require_membership(db, team_id, user.id)
    if member.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="仅所有者/管理员可编辑权限")
    team.policy = gov.ensure_policy(payload.policy)
    await db.commit()
    return PolicyOut(
        my_role=member.role,
        editable=True,
        permissions=gov.grouped_permissions(),
        policy=team.policy,
    )


# ── shared profiles ──
@router.put("/teams/{team_id}/shared-profiles", response_model=TeamDetail)
async def set_shared_profiles(
    team_id: uuid.UUID, payload: SharedProfilesUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    team, _m = await svc.require_permission(db, team_id, user.id, "agent.manage")
    await svc.set_shared_profiles(db, team, payload.profile_ids)
    return await _team_detail(db, team_id, user)


# ── knowledge ──
@router.get("/teams/{team_id}/knowledge", response_model=list[KnowledgeOut])
async def list_knowledge(
    team_id: uuid.UUID,
    folder_id: uuid.UUID | None = Query(None),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """List knowledge items. If folder_id given, only items in that folder.
    If folder_id is None, returns root-level items (folder_id IS NULL)."""
    from app.db.models.team import TeamKnowledge
    await svc.require_membership(db, team_id, user.id)
    stmt = select(TeamKnowledge).where(TeamKnowledge.team_id == team_id)
    if folder_id is not None:
        stmt = stmt.where(TeamKnowledge.folder_id == folder_id)
    else:
        stmt = stmt.where(TeamKnowledge.folder_id.is_(None))
    stmt = stmt.order_by(TeamKnowledge.is_folder.desc(), TeamKnowledge.sort_order, TeamKnowledge.created_at)
    rows = (await db.execute(stmt)).scalars().all()
    return [KnowledgeOut.model_validate(k) for k in rows]


@router.post("/teams/{team_id}/knowledge", response_model=KnowledgeOut, status_code=201)
async def add_knowledge(
    team_id: uuid.UUID, payload: KnowledgeCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await svc.require_permission(db, team_id, user.id, "knowledge.upload")
    return await svc.add_knowledge(db, team_id, payload, user)


@router.post("/teams/{team_id}/knowledge/folder", response_model=KnowledgeOut, status_code=201)
async def create_knowledge_folder(
    team_id: uuid.UUID, payload: dict,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Create a knowledge folder (directory)."""
    from app.db.models.team import TeamKnowledge
    await svc.require_permission(db, team_id, user.id, "knowledge.upload")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="目录名不能为空")
    parent_id = payload.get("folder_id")
    folder = TeamKnowledge(
        team_id=team_id,
        name=name,
        kind="folder",
        is_folder=True,
        folder_id=uuid.UUID(parent_id) if parent_id else None,
        uploaded_by=user.id,
        uploaded_by_name=user.name,
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return KnowledgeOut.model_validate(folder)


@router.patch("/teams/{team_id}/knowledge/{kid}/move")
async def move_knowledge(
    team_id: uuid.UUID, kid: uuid.UUID, payload: dict,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Move a knowledge item to a different folder."""
    from app.db.models.team import TeamKnowledge
    await svc.require_permission(db, team_id, user.id, "knowledge.upload")
    item = await db.get(TeamKnowledge, kid)
    if item is None or item.team_id != team_id:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    target_folder = payload.get("folder_id")  # None = root
    item.folder_id = uuid.UUID(target_folder) if target_folder else None
    await db.commit()
    return {"status": "ok"}


@router.delete("/teams/{team_id}/knowledge/{kid}", status_code=204)
async def delete_knowledge(
    team_id: uuid.UUID, kid: uuid.UUID,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await svc.require_permission(db, team_id, user.id, "knowledge.delete")
    await svc.delete_knowledge(db, team_id, kid)


@router.patch("/teams/{team_id}/knowledge/{kid}", response_model=KnowledgeOut)
async def update_knowledge(
    team_id: uuid.UUID, kid: uuid.UUID, payload: KnowledgeUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await svc.require_permission(db, team_id, user.id, "knowledge.upload")
    k = await svc.update_knowledge(db, team_id, kid, payload, author=user.name or str(user.id))
    if k is None:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return k


@router.get("/teams/{team_id}/knowledge/{kid}/versions", response_model=list[KnowledgeVersionOut])
async def list_knowledge_versions(
    team_id: uuid.UUID, kid: uuid.UUID,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    from app.db.models.team import TeamKnowledge
    await svc.require_membership(db, team_id, user.id)
    k = await db.get(TeamKnowledge, kid)
    if k is None or k.team_id != team_id:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    versions = await svc.list_knowledge_versions(db, kid)
    return [KnowledgeVersionOut.model_validate(v) for v in versions]


@router.post("/teams/{team_id}/knowledge/{kid}/restore/{version_num}", response_model=KnowledgeDetail)
async def restore_knowledge_version(
    team_id: uuid.UUID, kid: uuid.UUID, version_num: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await svc.require_permission(db, team_id, user.id, "knowledge.upload")
    k = await svc.restore_knowledge_version(
        db, team_id, kid, version_num, author=user.name or str(user.id)
    )
    if k is None:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return KnowledgeDetail(**KnowledgeOut.model_validate(k).model_dump(), content=k.content)


@router.get("/teams/{team_id}/knowledge/{kid}", response_model=KnowledgeDetail)
async def get_knowledge_content(
    team_id: uuid.UUID, kid: uuid.UUID,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    from app.db.models.team import TeamKnowledge
    await svc.require_membership(db, team_id, user.id)
    k = await db.get(TeamKnowledge, kid)
    if k is None or k.team_id != team_id:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return KnowledgeDetail(
        **KnowledgeOut.model_validate(k).model_dump(),
        content=k.content,
    )


@router.get("/teams/{team_id}/knowledge/{kid}/raw")
async def get_knowledge_raw(
    team_id: uuid.UUID, kid: uuid.UUID,
    request: Request,
    ticket: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import Response
    from app.db.models.team import TeamKnowledge
    from app.deps import user_from_ticket_or_header
    user = await user_from_ticket_or_header(ticket, request, db)
    await svc.require_membership(db, team_id, user.id)
    k = await db.get(TeamKnowledge, kid)
    if k is None or k.team_id != team_id:
        raise HTTPException(status_code=404, detail="知识条目不存在")

    MIME = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
        "bmp": "image/bmp", "pdf": "application/pdf",
        "txt": "text/plain", "md": "text/markdown", "html": "text/html",
        "htm": "text/html", "csv": "text/csv", "json": "application/json",
    }
    ext = k.name.rsplit(".", 1)[-1].lower() if "." in k.name else ""
    mime = MIME.get(ext, "application/octet-stream")

    if not k.content:
        raise HTTPException(status_code=404, detail="文件内容不存在")

    import base64
    TEXT_EXTS = {"md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
                 "yaml", "yml", "toml", "sh", "log", "xml", "css", "diff", "patch"}
    if ext in TEXT_EXTS:
        data = k.content.encode("utf-8")
    else:
        try:
            data = base64.b64decode(k.content.strip())
        except Exception:
            data = k.content.encode("utf-8")

    from urllib.parse import quote
    safe_name = k.name.replace('"', "_").replace("\\", "_")
    ascii_name = safe_name.encode("ascii", "ignore").decode() or "file"
    return Response(
        content=data, media_type=mime,
        headers={
            "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe_name)}"
        },
    )


@router.post("/teams/{team_id}/knowledge/upload", response_model=KnowledgeOut, status_code=201)
async def upload_knowledge(
    team_id: uuid.UUID,
    file: UploadFile = FastApiFile(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.db.models.team import TeamKnowledge
    await svc.require_permission(db, team_id, user.id, "knowledge.upload")
    raw = await read_upload_capped(file, settings.max_upload_bytes)
    import re as _re
    name = _re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", file.filename or "upload").strip("_. ") or "upload"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "bin"
    from app.core.files import OFFICE_EXTRACTORS
    TEXT_EXTS = {"md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
                 "yaml", "yml", "toml", "sh", "log", "xml", "css", "diff", "patch"}
    if ext in OFFICE_EXTRACTORS:
        content = OFFICE_EXTRACTORS[ext](raw) or "<p><em>(\u65e0\u6cd5\u89e3\u6790\u6587\u6863\u5185\u5bb9)</em></p>"
    elif ext in TEXT_EXTS:
        content = raw.decode("utf-8", "ignore")
    elif ext == "pdf":
        try:
            import fitz
            doc = fitz.open(stream=raw, filetype="pdf")
            content = "\n".join(str(page.get_text()) for page in doc)
            doc.close()
        except Exception:
            import base64
            content = base64.b64encode(raw).decode("ascii")
    elif ext in ("xlsx", "xls"):
        try:
            import io
            import pandas as pd
            dfs = pd.read_excel(io.BytesIO(raw), sheet_name=None)
            parts = []
            for sheet_name, df in dfs.items():
                parts.append(f"[Sheet: {sheet_name}]\n{df.to_string(index=False)}")
            content = "\n\n".join(parts)
        except Exception:
            import base64
            content = base64.b64encode(raw).decode("ascii")
    else:
        import base64
        content = base64.b64encode(raw).decode("ascii")

    k = TeamKnowledge(
        team_id=team_id,
        name=name,
        kind=ext,
        size_bytes=len(raw),
        content=content,
        uploaded_by=user.id,
        uploaded_by_name=user.name,
    )
    db.add(k)
    await db.commit()
    await db.refresh(k)
    return KnowledgeOut.model_validate(k)


# ── projects ──
@router.get("/teams/{team_id}/projects", response_model=list[ProjectOut])
async def list_projects(
    team_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    await svc.require_membership(db, team_id, user.id)
    return await svc.list_projects(db, team_id)


@router.post("/teams/{team_id}/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    team_id: uuid.UUID, payload: ProjectCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await svc.require_permission(db, team_id, user.id, "project.create")
    return await svc.create_project(db, team_id, payload, owner=user)


async def _project_with_perm(db, project_id, user, perm) -> object:
    proj = await svc.get_project(db, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await svc.require_permission(db, proj.team_id, user.id, perm)
    return proj


@router.put("/projects/{project_id}/members", response_model=ProjectOut)
async def set_project_members(
    project_id: uuid.UUID, payload: ProjectMembersUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    proj = await _project_with_perm(db, project_id, user, "project.edit")
    return await svc.set_project_members(db, proj, payload.user_ids)


@router.get("/projects/{project_id}/docs", response_model=list[DocOut])
async def list_docs(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    proj = await svc.get_project(db, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await svc.require_membership(db, proj.team_id, user.id)
    return await svc.list_docs(db, project_id)


@router.post("/projects/{project_id}/docs", response_model=DocOut, status_code=201)
async def add_doc(
    project_id: uuid.UUID, payload: DocCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await _project_with_perm(db, project_id, user, "project.edit")
    return await svc.add_doc(db, project_id, payload, user)


@router.post("/projects/{project_id}/docs/upload", response_model=DocOut, status_code=201)
async def upload_doc(
    project_id: uuid.UUID,
    file: UploadFile = FastApiFile(...),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Upload a real file as a project document."""
    await _project_with_perm(db, project_id, user, "project.edit")
    content = await read_upload_capped(file, settings.max_upload_bytes)
    kind = (file.filename or "doc").rsplit(".", 1)[-1] if "." in (file.filename or "") else "doc"
    payload = DocCreate(
        name=file.filename or "upload",
        kind=kind,
        size_bytes=len(content),
    )
    doc = await svc.add_doc(db, project_id, payload, user)
    # Store file content in object storage if available
    try:
        import asyncio
        from app.core import object_storage
        storage_key = f"project-docs/{doc.id}/{file.filename}"
        await asyncio.to_thread(object_storage.put, storage_key, content)
    except Exception:
        pass  # Graceful degradation: metadata saved even if storage fails
    return doc


@router.delete("/projects/docs/{doc_id}", status_code=204)
async def delete_doc(
    doc_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    from app.db.models.team import ProjectDoc

    d = await db.get(ProjectDoc, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    await _project_with_perm(db, d.project_id, user, "project.edit")
    await svc.delete_doc(db, doc_id)


@router.patch("/projects/docs/{doc_id}", response_model=DocOut)
async def update_doc(
    doc_id: uuid.UUID, payload: DocContentUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    from app.db.models.team import ProjectDoc

    d = await db.get(ProjectDoc, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    await _project_with_perm(db, d.project_id, user, "project.edit")
    updated = await svc.update_doc_content(
        db, d.project_id, doc_id, payload.content, author=user.name or str(user.id)
    )
    return updated


@router.get("/projects/docs/{doc_id}/versions", response_model=list[DocVersionOut])
async def list_doc_versions(
    doc_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    from app.db.models.team import ProjectDoc

    d = await db.get(ProjectDoc, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    proj = await svc.get_project(db, d.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await svc.require_membership(db, proj.team_id, user.id)
    versions = await svc.list_doc_versions(db, doc_id)
    return [DocVersionOut.model_validate(v) for v in versions]


@router.post("/projects/docs/{doc_id}/restore/{version_num}", response_model=DocOut)
async def restore_doc_version(
    doc_id: uuid.UUID, version_num: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    from app.db.models.team import ProjectDoc

    d = await db.get(ProjectDoc, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    await _project_with_perm(db, d.project_id, user, "project.edit")
    updated = await svc.restore_doc_version(
        db, d.project_id, doc_id, version_num, author=user.name or str(user.id)
    )
    return updated


@router.get("/projects/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    proj = await svc.get_project(db, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await svc.require_membership(db, proj.team_id, user.id)
    member_rows = await svc.members_by_ids(db, proj.team_id, list(proj.member_ids or []))
    docs = await svc.list_docs(db, project_id)
    convos = await svc.project_conversations(db, project_id)
    return ProjectDetail(
        **ProjectOut.model_validate(proj).model_dump(),
        members=[_member_out(m, u) for m, u in member_rows],
        docs=[DocOut.model_validate(d) for d in docs],
        conversations=[ConversationBrief.model_validate(c) for c in convos],
    )


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    proj = await _project_with_perm(db, project_id, user, "project.edit")
    for f, v in payload.model_dump(exclude_unset=True).items():
        setattr(proj, f, v)
    await db.commit()
    await db.refresh(proj)
    return proj


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    proj = await _project_with_perm(db, project_id, user, "project.delete")
    await db.delete(proj)
    await db.commit()


# ── tasks ──
@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
async def list_tasks(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    proj = await svc.get_project(db, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await svc.require_membership(db, proj.team_id, user.id)
    return await svc.list_tasks(db, project_id)


@router.post("/projects/{project_id}/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    project_id: uuid.UUID, payload: TaskCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await _project_with_perm(db, project_id, user, "project.edit")
    return await svc.create_task(db, project_id, payload)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID, payload: TaskUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    from app.db.models.team import ProjectTask

    task = await db.get(ProjectTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await _project_with_perm(db, task.project_id, user, "project.edit")
    fields = payload.model_dump(exclude_unset=True)
    new_status = fields.pop("status", None)
    for f, v in fields.items():
        setattr(task, f, v)
    if fields:
        await db.commit()
        await db.refresh(task)
    # Route status changes through the single source of truth (progress + activity + notify)
    if new_status is not None and new_status != task.status:
        task = await svc.move_task_status(db, task, new_status, user)
    return task


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    from app.db.models.team import ProjectTask

    task = await db.get(ProjectTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await _project_with_perm(db, task.project_id, user, "project.edit")
    await db.delete(task)
    await db.commit()


@router.put("/projects/{project_id}/tasks/reorder")
async def reorder_tasks(
    project_id: uuid.UUID,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Batch-update task order_idx and status (drag-to-reorder on Kanban)."""
    from app.db.models.team import ProjectTask
    from sqlalchemy import select

    await _project_with_perm(db, project_id, user, "project.edit")
    items = payload.get("items", [])
    if not items:
        return {"status": "ok"}
    ids = [item["id"] for item in items if item.get("id")]
    rows = {
        t.id: t
        for t in (
            await db.execute(
                select(ProjectTask).where(
                    ProjectTask.id.in_(ids),
                    ProjectTask.project_id == project_id,
                )
            )
        ).scalars().all()
    }
    status_changed: list = []
    for item in items:
        t = rows.get(uuid.UUID(item["id"]))
        if t:
            t.order_idx = item.get("order_idx", 0)
            if "status" in item and item["status"] != t.status:
                t.status = item["status"]
                status_changed.append(t)
    await db.commit()
    # Recompute progress + log/notify when any status changed (single source of truth)
    if status_changed:
        proj = await svc.get_project(db, project_id)
        if proj is not None:
            await svc.recompute_progress(db, proj)
            for t in status_changed:
                await svc.log_activity(
                    db, project=proj, actor=user,
                    kind="task.done" if t.status == "done" else "task.moved",
                    summary=("完成了任务" if t.status == "done" else "推进了任务") + f"「{t.title}」",
                    meta={"task_id": str(t.id), "to_status": t.status}, commit=False,
                )
            await db.commit()
            await svc.notify_project_members(
                db, proj, title=proj.name,
                snippet=f"看板更新了 {len(status_changed)} 个任务", actor_id=user.id,
            )
    return {"status": "ok"}


# ── closed-loop: task status / derive / activity ──
@router.patch("/tasks/{task_id}/status", response_model=TaskOut)
async def move_task_status(
    task_id: uuid.UUID, payload: TaskStatusUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Advance a single task's status → auto progress, activity log, notify."""
    from app.db.models.team import ProjectTask

    task = await db.get(ProjectTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await _project_with_perm(db, task.project_id, user, "task.move")
    return await svc.move_task_status(db, task, payload.status, user)


@router.post("/projects/{project_id}/tasks/from-conversation", response_model=list[TaskOut])
async def tasks_from_conversation(
    project_id: uuid.UUID, payload: TaskFromConversation,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Derive tasks from a conversation message's bullet list."""
    from app.services import conversation_service as conv_svc

    proj = await _project_with_perm(db, project_id, user, "task.derive")
    message = await conv_svc.get_message(db, payload.message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    return await conv_svc.auto_create_tasks_from_message(
        db, message=message, project=proj, actor=user
    )


@router.post("/tasks/{task_id}/execute")
async def execute_task(
    task_id: uuid.UUID, payload: dict,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Execute a task with the specified profile via the agent runner."""
    from app.services import conversation_service as conv_svc
    from app.db.models.team import ProjectTask

    profile_id = payload.get("profile_id")
    if not profile_id:
        raise HTTPException(status_code=400, detail="缺少 profile_id")

    task = await db.get(ProjectTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await _project_with_perm(db, task.project_id, user, "task.move")

    try:
        result = await conv_svc.execute_task_with_profile(
            db, task_id=uuid.UUID(str(task_id)),
            profile_id=uuid.UUID(str(profile_id)),
            actor_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/projects/{project_id}/activity", response_model=list[ActivityOut])
async def project_activity(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    proj = await svc.get_project(db, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    await svc.require_membership(db, proj.team_id, user.id)
    return await svc.list_project_activity(db, project_id)


@router.get("/projects/docs/{doc_id}/raw")
async def get_doc_raw(
    doc_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    d = await svc.get_doc(db, doc_id)
    if d is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    await svc.require_membership(db, (await svc.get_project(db, d.project_id)).team_id, user.id)
    return {"id": str(d.id), "name": d.name, "kind": d.kind, "content": d.content}


# ── helpers ──
def _member_out(member, u: User | None) -> MemberOut:
    return MemberOut(
        user_id=member.user_id,
        role=member.role,
        status=member.status,
        joined_at=member.joined_at,
        name=u.name if u else None,
        email=u.email if u else None,
        initials=u.initials if u else None,
        color=u.color if u else None,
    )


async def _team_detail(db: AsyncSession, team_id: uuid.UUID, user: User) -> TeamDetail:
    from app.db.models.team import Team

    team = await db.get(Team, team_id)
    rows = await svc.list_members(db, team_id)
    my = next((m for m, _ in rows if m.user_id == user.id), None)
    shared_profiles = list(team.shared_profile_ids or [])
    knowledge = await svc.list_knowledge(db, team_id)
    pinned = await svc.team_pinned(db, team_id)
    activity = await svc.team_activity(db, team)
    return TeamDetail(
        **TeamOut.model_validate(team).model_dump(),
        my_role=my.role if my else "viewer",
        members=[_member_out(m, u) for m, u in rows],
        shared_profile_ids=shared_profiles,
        stats=TeamStats(
            members=len(rows),
            agents=len(shared_profiles),
            threads=await svc.team_threads_count(db, team_id),
            knowledge=len(knowledge),
        ),
        knowledge=[KnowledgeOut.model_validate(k) for k in knowledge],
        activity=[ActivityItem(**a) for a in activity],
        pinned=[ConversationBrief.model_validate(c) for c in pinned],
    )
