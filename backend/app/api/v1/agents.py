"""Agent registry (from ACP discovery) + Profile CRUD."""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models.agent import Agent, Profile
from app.db.models.user import User
from app.deps import get_current_user
from app.core.guards import require_permission
from app.schemas.agent import (
    AgentOut,
    ProfileCreate,
    ProfileExport,
    ProfileOut,
    ProfileUpdate,
    ScanProfilesResponse,
)

router = APIRouter()


# ── Agents ──

@router.get("/agents", response_model=list[AgentOut], dependencies=[Depends(get_current_user)])
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[Agent]:
    res = await db.execute(select(Agent).order_by(Agent.available.desc(), Agent.id))
    return list(res.scalars().all())


class ScanAgentsResponse(BaseModel):
    found: int
    created: int
    updated: int
    agents: list[AgentOut]
    version: str | None = None
    hermes_path: str | None = None
    errors: list[str] = []


@router.post("/agents/scan", response_model=ScanAgentsResponse)
async def scan_agents(
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Discover ACP agents from PATH and upsert into DB."""

    from agent_runner.discovery import find_hermes_binary, probe_hermes_version, scan

    errors: list[str] = []
    try:
        discovered = await scan()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"扫描失败: {exc}")

    try:
        version = await probe_hermes_version()
    except Exception:
        version = "unknown"

    hermes_path = find_hermes_binary()
    created = 0
    updated = 0
    now = datetime.now(timezone.utc)

    # Batch-load all existing agents in one query instead of N db.get calls.
    discovered_ids = [da.id for da in discovered]
    existing_agents: dict = {}
    if discovered_ids:
        existing_rows = (await db.execute(
            select(Agent).where(Agent.id.in_(discovered_ids))
        )).scalars().all()
        existing_agents = {a.id: a for a in existing_rows}

    for da in discovered:
        existing = existing_agents.get(da.id)
        if existing:
            existing.label = da.label
            existing.kind = da.kind
            existing.available = da.available
            existing.official = da.official
            existing.version = da.version
            existing.color = da.color
            existing.icon = da.icon
            existing.description = da.description
            existing.command = da.command
            existing.last_seen_at = now
            updated += 1
        else:
            agent = Agent(
                id=da.id,
                label=da.label,
                kind=da.kind,
                available=da.available,
                official=da.official,
                version=da.version,
                color=da.color,
                icon=da.icon,
                description=da.description,
                command=da.command,
                last_seen_at=now,
            )
            db.add(agent)
            created += 1

    await db.commit()

    # Re-fetch all agents for response
    res = await db.execute(select(Agent).order_by(Agent.available.desc(), Agent.id))
    all_agents = list(res.scalars().all())

    return ScanAgentsResponse(
        found=len(discovered),
        created=created,
        updated=updated,
        agents=[AgentOut.model_validate(a) for a in all_agents],
        version=version,
        hermes_path=hermes_path,
        errors=errors,
    )


# ── Profiles ──

@router.get("/profiles", response_model=list[ProfileOut], dependencies=[Depends(get_current_user)])
async def list_profiles(db: AsyncSession = Depends(get_db)) -> list[ProfileOut]:
    res = await db.execute(
        select(Profile).where(Profile.is_active.is_(True)).order_by(Profile.created_at)
    )
    profiles = list(res.scalars().all())
    # Fall back to hardcoded defaults if DB is empty (before migration runs).
    if not profiles:
        return [
            ProfileOut(
                id=uuid.uuid4(), name="主信使", handle="hermes-main", scope="personal",
                color="#b8852a", icon="brand", desc="默认 Hermes Agent，连接本机 ACP 会话",
                default_agent_id="hermes", default_model="hermes-4",
            ),
        ]
    return [ProfileOut.model_validate(p) for p in profiles]


def _serialize_skills(data: dict) -> dict:
    """Convert skills list → JSON string for DB storage."""
    if "skills" in data and isinstance(data["skills"], list):
        data["skills"] = json.dumps(data["skills"], ensure_ascii=False)
    return data


@router.post("/profiles", response_model=ProfileOut, status_code=201)
async def create_profile(
    payload: ProfileCreate,
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    data = _serialize_skills(payload.model_dump())
    p = Profile(**data)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return ProfileOut.model_validate(p)


@router.patch("/profiles/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: uuid.UUID,
    payload: ProfileUpdate,
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(Profile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="助手不存在")
    for field, value in _serialize_skills(payload.model_dump(exclude_unset=True)).items():
        setattr(p, field, value)
    await db.commit()
    await db.refresh(p)
    return ProfileOut.model_validate(p)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: uuid.UUID,
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(Profile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="助手不存在")
    await db.delete(p)
    await db.commit()


@router.post("/profiles/{profile_id}/clone", response_model=ProfileOut, status_code=201)
async def clone_profile(
    profile_id: uuid.UUID,
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Clone an existing profile — copies all fields, generates a new handle."""
    p = await db.get(Profile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="助手不存在")
    clone = Profile(
        name=f"{p.name} (副本)",
        handle=f"{p.handle}-copy-{uuid.uuid4().hex[:6]}",
        scope=p.scope,
        color=p.color,
        icon=p.icon,
        desc=p.desc,
        default_agent_id=p.default_agent_id,
        default_model=p.default_model,
        team_id=p.team_id,
        path=p.path,
        system_prompt=p.system_prompt,
        skills=p.skills,
        featured=False,
    )
    db.add(clone)
    await db.commit()
    await db.refresh(clone)
    return ProfileOut.model_validate(clone)


@router.get("/profiles/{profile_id}/export", response_model=ProfileExport)
async def export_profile(
    profile_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export a profile as a portable JSON object (no team_id, no path)."""
    p = await db.get(Profile, profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="助手不存在")
    skills: list[str] = []
    if p.skills:
        try:
            skills = json.loads(p.skills)
        except (json.JSONDecodeError, ValueError):
            skills = []
    return ProfileExport(
        name=p.name,
        handle=p.handle,
        scope=p.scope,
        color=p.color,
        icon=p.icon,
        desc=p.desc,
        default_agent_id=p.default_agent_id,
        default_model=p.default_model,
        system_prompt=p.system_prompt,
        skills=skills,
        featured=p.featured,
    )


class ProfileImportRequest(BaseModel):
    """Payload for importing one or more profiles."""
    profiles: list[ProfileExport]


@router.post("/profiles/import", response_model=list[ProfileOut], status_code=201)
async def import_profiles(
    payload: ProfileImportRequest,
    user: User = Depends(require_permission("agent.import")),
    db: AsyncSession = Depends(get_db),
):
    """Import profiles from exported JSON. Skips duplicates by handle."""
    existing = await db.execute(select(Profile.handle))
    existing_handles = set(existing.scalars().all())
    created: list[Profile] = []
    for item in payload.profiles:
        handle = item.handle
        if handle in existing_handles:
            handle = f"{item.handle}-{uuid.uuid4().hex[:6]}"
        p = Profile(
            name=item.name,
            handle=handle,
            scope=item.scope,
            color=item.color,
            icon=item.icon,
            desc=item.desc,
            default_agent_id=item.default_agent_id,
            default_model=item.default_model,
            system_prompt=item.system_prompt,
            skills=json.dumps(item.skills, ensure_ascii=False) if item.skills else None,
            featured=item.featured,
        )
        db.add(p)
        created.append(p)
        existing_handles.add(handle)
    await db.commit()
    for p in created:
        await db.refresh(p)
    return [ProfileOut.model_validate(p) for p in created]


@router.post("/profiles/scan", response_model=ScanProfilesResponse, status_code=200)
async def scan_profiles(
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Auto-generate profiles for agents and Hermes filesystem profiles.

    Never raises — all partial failures are collected in `errors` field so the
    frontend can show actionable diagnostics instead of a generic "failed" toast.
    """

    from agent_runner.discovery import (
        find_hermes_binary,
        get_hermes_home,
        list_hermes_fs_profiles,
        probe_hermes_version,
    )

    errors: list[str] = []

    # --- version probe ---
    try:
        version = await probe_hermes_version()
    except Exception as exc:  # noqa: BLE001
        version = "unknown"
        errors.append(f"版本探测失败: {exc}")

    hermes_path = find_hermes_binary()
    hermes_home = str(get_hermes_home())

    if hermes_path is None:
        errors.append(
            "未找到 hermes 可执行文件（已搜索 PATH 及常见安装目录）。"
            "如已安装，请设置环境变量 HERMES_BIN=/your/path/to/hermes 后重启服务。"
        )

    # --- load existing handles ---
    profiles_res = await db.execute(select(Profile.handle))
    existing_handles = set(profiles_res.scalars().all())
    created = 0

    # 1. Sync from Hermes filesystem profiles (~/.hermes/ or $HERMES_HOME)
    try:
        fs_profiles = list_hermes_fs_profiles()
    except Exception as exc:  # noqa: BLE001
        fs_profiles = []
        errors.append(f"扫描 {hermes_home} 失败: {exc}")

    if not fs_profiles and not errors:
        errors.append(
            f"未在 {hermes_home} 找到任何 profile（config.yaml）。"
            "如需让容器访问宿主机 profile，请挂载宿主机 ~/.hermes 目录，"
            "或设置 HERMES_HOME 环境变量。"
        )

    for fsp in fs_profiles:
        if fsp["handle"] in existing_handles:
            continue
        try:
            p = Profile(
                name=fsp["name"],
                handle=fsp["handle"],
                scope="personal",
                color="#b8852a",
                icon="brand",
                desc="",
                default_agent_id="hermes",
                default_model=fsp.get("model", "hermes-4"),
                path=fsp.get("path"),
            )
            db.add(p)
            existing_handles.add(fsp["handle"])
            created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"创建 profile '{fsp.get('handle')}' 失败: {exc}")

    # 2. Sync from registered Agent records
    try:
        agents_res = await db.execute(select(Agent).where(Agent.available.is_(True)))
        for a in agents_res.scalars().all():
            handle = f"agent-{a.id}"
            if handle in existing_handles:
                continue
            p = Profile(
                name=a.label,
                handle=handle,
                scope="global",
                color=a.color or "#b8852a",
                icon=a.icon or "sparkle",
                desc=a.description or "",
                default_agent_id=a.id,
                default_model="hermes-4",
            )
            db.add(p)
            existing_handles.add(handle)
            created += 1
    except Exception as exc:  # noqa: BLE001
        errors.append(f"同步 Agent 记录失败: {exc}")

    if created:
        try:
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            errors.append(f"保存到数据库失败: {exc}")
            created = 0

    parts = []
    if created:
        parts.append(f"新增 {created} 个助手")
    if fs_profiles:
        parts.append(f"发现 {len(fs_profiles)} 个 profile")
    if hermes_path:
        parts.append(f"版本 {version}")
    message = "；".join(parts) if parts else "未发现新助手"

    return ScanProfilesResponse(
        created=created,
        message=message,
        version=version,
        profiles_found=len(fs_profiles),
        hermes_path=hermes_path,
        hermes_home=hermes_home,
        errors=errors,
    )
