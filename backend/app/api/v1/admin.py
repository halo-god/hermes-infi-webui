"""Admin console: user management, audit log, system settings, stats.

All routes require an admin (super_admin/admin) platform role.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel as _BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.rbac import PERMISSION_CATALOG, ROLE_META, ROLE_ORDER, require_admin  # noqa: F401
from app.db.base import get_db
from app.db.models.audit import AuditLog  # noqa: F401
from app.db.models.conversation import Conversation, Message
from app.db.models.team import Team
from app.db.models.agent import Profile
from app.db.models.user import User
from app.schemas.admin import (
    AdminStats,
    AdminUserUpdate,
    AuditEntryOut,
    MappingCreate,
    MappingOut,
    PermissionGroup,
    ProviderOut,
    ProviderUpdate,
    RoleOut,
    RolesMatrixOut,
    SystemSettingsOut,
    SystemSettingsUpdate,
)
from app.schemas.user import UserCreate, UserOut
from app.services import (
    audit_service,
    identity_service,
    settings_service,
    user_service,
)

router = APIRouter(dependencies=[Depends(require_admin())])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ── dashboard ──
@router.get("/stats", response_model=AdminStats)
async def stats(db: AsyncSession = Depends(get_db)):
    async def count(model) -> int:
        return int((await db.execute(select(func.count()).select_from(model))).scalar() or 0)

    # role / source / status distributions in one grouped pass each
    role_rows = (await db.execute(select(User.role, func.count()).group_by(User.role))).all()
    source_rows = (await db.execute(select(User.source, func.count()).group_by(User.source))).all()
    status_rows = (await db.execute(select(User.status, func.count()).group_by(User.status))).all()
    status_dist = {s or "active": int(n) for s, n in status_rows}

    return AdminStats(
        users=await count(User),
        teams=await count(Team),
        conversations=await count(Conversation),
        messages=await count(Message),
        agents=await count(Profile),
        active_users=status_dist.get("active", 0),
        pending_users=status_dist.get("pending", 0),
        role_distribution={r or "member": int(n) for r, n in role_rows},
        source_distribution={s or "local": int(n) for s, n in source_rows},
    )


# ── roles & permission matrix ──
def _build_permissions(overrides: dict) -> list[dict]:
    """Merge hardcoded PERMISSION_CATALOG with stored overrides."""
    import copy
    catalog = copy.deepcopy(PERMISSION_CATALOG)
    for group in catalog:
        for item in group["items"]:
            if item["id"] in overrides:
                item["roles"] = overrides[item["id"]]
    return catalog


@router.get("/performance")
async def performance(db: AsyncSession = Depends(get_db)):
    """System performance metrics for the admin dashboard."""
    import psutil
    import time
    from app.core import redis as redis_core
    from app.db.models.conversation import Conversation, Message

    # CPU & memory
    process = psutil.Process()
    mem_info = process.memory_info()
    cpu_percent = process.cpu_percent(interval=0.1)
    sys_mem = psutil.virtual_memory()

    # DB counts
    conv_count = int((await db.execute(select(func.count()).select_from(Conversation))).scalar() or 0)
    msg_count = int((await db.execute(select(func.count()).select_from(Message))).scalar() or 0)
    user_count = int((await db.execute(select(func.count()).select_from(User))).scalar() or 0)

    # Redis status
    redis_ok = False
    redis_info: dict = {}
    try:
        r = redis_core.get_redis()
        redis_ok = await r.ping()
        redis_info = await r.info("memory")
    except Exception:  # noqa: BLE001
        pass

    # Uptime
    uptime_seconds = int(time.time() - process.create_time())

    return {
        "process": {
            "cpu_percent": round(cpu_percent, 1),
            "memory_mb": round(mem_info.rss / 1024 / 1024, 1),
            "memory_percent": round(mem_info.rss / sys_mem.total * 100, 1),
            "threads": process.num_threads(),
            "uptime_seconds": uptime_seconds,
        },
        "system": {
            "cpu_percent": round(psutil.cpu_percent(interval=0.1), 1),
            "memory_total_mb": round(sys_mem.total / 1024 / 1024, 1),
            "memory_used_mb": round(sys_mem.used / 1024 / 1024, 1),
            "memory_percent": round(sys_mem.percent, 1),
            "disk_percent": round(psutil.disk_usage("/").percent, 1) if hasattr(psutil, "disk_usage") else None,
        },
        "database": {
            "conversations": conv_count,
            "messages": msg_count,
            "users": user_count,
        },
        "redis": {
            "connected": redis_ok,
            "used_memory_mb": round(redis_info.get("used_memory", 0) / 1024 / 1024, 1) if redis_info else 0,
        },
    }


@router.get("/roles", response_model=RolesMatrixOut)
async def roles(db: AsyncSession = Depends(get_db)):
    """Platform RBAC: role catalog (with live user counts) + permission matrix."""
    role_rows = (await db.execute(select(User.role, func.count()).group_by(User.role))).all()
    counts = {r or "member": int(n) for r, n in role_rows}
    settings = await settings_service.get(db)
    overrides: dict = (settings.data or {}).get("permission_overrides", {})
    return RolesMatrixOut(
        roles=[
            RoleOut(
                id=m["id"], name=m["name"], desc=m["desc"],
                system=m["system"], users=counts.get(m["id"], 0),
            )
            for m in ROLE_META
        ],
        permissions=[PermissionGroup(**g) for g in _build_permissions(overrides)],
    )


class PermissionToggle(_BaseModel):
    perm_id: str
    role: str
    granted: bool


@router.patch("/roles/permissions")
async def toggle_permission(
    payload: PermissionToggle,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Toggle a permission-role assignment. super_admin only."""
    if ROLE_ORDER.get(admin.role, 0) < ROLE_ORDER.get("super_admin", 0):
        raise HTTPException(status_code=403, detail="仅超级管理员可修改权限矩阵")
    # Find the permission in the catalog
    found = False
    for group in PERMISSION_CATALOG:
        for item in group["items"]:
            if item["id"] == payload.perm_id:
                found = True
                break
    if not found:
        raise HTTPException(status_code=404, detail="权限不存在")
    if payload.role not in ROLE_ORDER:
        raise HTTPException(status_code=422, detail="角色不存在")

    settings = await settings_service.get(db)
    data = dict(settings.data or {})
    overrides: dict = dict(data.get("permission_overrides", {}))

    # Get current roles for this permission (from overrides or catalog)
    current_roles: list[str] = overrides.get(payload.perm_id, None)
    if current_roles is None:
        for group in PERMISSION_CATALOG:
            for item in group["items"]:
                if item["id"] == payload.perm_id:
                    current_roles = list(item["roles"])
                    break

    current_roles = list(current_roles or [])
    if payload.granted and payload.role not in current_roles:
        current_roles.append(payload.role)
    elif not payload.granted and payload.role in current_roles:
        current_roles.remove(payload.role)

    overrides[payload.perm_id] = current_roles
    data["permission_overrides"] = overrides
    await settings_service.update(db, data)
    return {"perm_id": payload.perm_id, "role": payload.role, "granted": payload.granted}


# ── user management ──
@router.get("/users", response_model=list[UserOut])
async def list_users(
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User).order_by(User.created_at.desc()).limit(500)
    if q:
        escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        stmt = stmt.where(func.lower(User.name).like(like, escape="\\") | func.lower(User.email).like(like, escape="\\"))
    return list((await db.execute(stmt)).scalars().all())


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreate,
    request: Request,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    if await user_service.get_by_email(db, str(payload.email)):
        raise HTTPException(status_code=409, detail="该邮箱已存在")
    user = await user_service.create_user(db, payload)
    await audit_service.record(
        action="admin.user.create", actor_id=admin.id, actor_name=admin.name,
        target=user.email, ip=_ip(request), meta={"role": user.role},
    )
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    request: Request,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    user = await user_service.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    changes = payload.model_dump(exclude_unset=True)
    for f, v in changes.items():
        setattr(user, f, v)
    if changes.get("status") == "inactive":
        user.is_active = False
    await db.commit()
    await db.refresh(user)
    await audit_service.record(
        action="admin.user.update", actor_id=admin.id, actor_name=admin.name,
        target=user.email, ip=_ip(request), meta=changes,
    )
    return user


# ── audit log ──
@router.get("/audit", response_model=list[AuditEntryOut])
async def audit(
    action: str | None = Query(None),
    result: str | None = Query(None),
    limit: int = Query(100, le=500),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await audit_service.query(
        db, action=action, result=result, limit=limit,
        date_from=date_from, date_to=date_to,
    )


# ── system settings ──
@router.get("/settings", response_model=SystemSettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)):
    return await settings_service.get(db)


@router.put("/settings", response_model=SystemSettingsOut)
async def put_settings(
    payload: SystemSettingsUpdate,
    request: Request,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    s = await settings_service.update(db, payload.data)
    # propagate the editable rate limit to the live limiter
    try:
        rpm = int(payload.data.get("model_gateway", {}).get("rate_limit_per_min"))
        await ratelimit.set_rate_limit(rpm)
    except (TypeError, ValueError):
        pass
    await audit_service.record(
        action="admin.settings.update", actor_id=admin.id, actor_name=admin.name,
        target="system", ip=_ip(request),
    )
    return s


# ── identity providers (LDAP/AD, WeCom, …) ──
@router.get("/identity", response_model=list[ProviderOut])
async def list_identity(db: AsyncSession = Depends(get_db)):
    return await identity_service.list_providers(db)


@router.patch("/identity/{pid}", response_model=ProviderOut)
async def update_identity(
    pid: str,
    payload: ProviderUpdate,
    request: Request,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    p = await identity_service.update_provider(
        db, pid, enabled=payload.enabled, config=payload.config
    )
    await audit_service.record(
        action="admin.identity.update", actor_id=admin.id, actor_name=admin.name,
        target=pid, ip=_ip(request), meta={"enabled": p.enabled},
    )
    return p


@router.get("/identity/{pid}/mappings", response_model=list[MappingOut])
async def list_mappings(pid: str, org: str | None = None, db: AsyncSession = Depends(get_db)):
    return await identity_service.list_mappings(db, pid, org_id=org)


@router.post("/identity/{pid}/mappings", response_model=MappingOut, status_code=201)
async def add_mapping(
    pid: str, payload: MappingCreate, db: AsyncSession = Depends(get_db)
):
    return await identity_service.add_mapping(db, pid, payload.model_dump())


@router.delete("/identity/mappings/{mapping_id}", status_code=204)
async def delete_mapping(mapping_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await identity_service.delete_mapping(db, mapping_id)


@router.post("/identity/{pid}/test")
async def test_identity(
    pid: str,
    org: str | None = None,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Test connectivity and credential validity for an identity provider."""
    return await identity_service.test_provider(db, pid, org_id=org)


# ── MCP Server management ──

class McpServerIn(_BaseModel):
    name: str
    transport: str  # "stdio" | "http"
    command: str | None = None
    url: str | None = None
    env: dict | None = None


class McpServerOut(_BaseModel):
    name: str
    transport: str
    command: str | None
    url: str | None
    env: dict | None


@router.get("/mcp-servers", response_model=list[McpServerOut])
async def list_mcp_servers(db: AsyncSession = Depends(get_db)):
    settings = await settings_service.get(db)
    servers: list[dict] = (settings.data or {}).get("mcp_servers", [])
    return [McpServerOut(**s) for s in servers]


@router.post("/mcp-servers", response_model=McpServerOut, status_code=201)
async def add_mcp_server(
    payload: McpServerIn,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    settings = await settings_service.get(db)
    data = dict(settings.data or {})
    servers: list[dict] = list(data.get("mcp_servers", []))
    if any(s["name"] == payload.name for s in servers):
        raise HTTPException(status_code=409, detail="名称已存在")
    entry = payload.model_dump()
    servers.append(entry)
    data["mcp_servers"] = servers
    await settings_service.update(db, data)
    return McpServerOut(**entry)


@router.delete("/mcp-servers/{name}", status_code=204)
async def delete_mcp_server(
    name: str,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    settings = await settings_service.get(db)
    data = dict(settings.data or {})
    servers: list[dict] = [s for s in data.get("mcp_servers", []) if s["name"] != name]
    data["mcp_servers"] = servers
    await settings_service.update(db, data)


@router.get("/mcp-servers/{name}/status")
async def mcp_server_status(name: str, db: AsyncSession = Depends(get_db)):
    """Check if an MCP server is reachable (basic HTTP health check).
    Blocks private/loopback IPs to prevent SSRF."""
    settings = await settings_service.get(db)
    servers: list[dict] = (settings.data or {}).get("mcp_servers", [])
    server = next((s for s in servers if s["name"] == name), None)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    import aiohttp
    import ipaddress
    import socket
    from urllib.parse import urlparse

    url = server.get("url") or server.get("base_url", "")
    if not url:
        return {"name": name, "status": "no_url", "reachable": False}

    # SSRF protection: resolve hostname and reject private/loopback/link-local IPs.
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"name": name, "status": "invalid_scheme", "reachable": False}
    hostname = parsed.hostname or ""
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for family, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return {"name": name, "status": "blocked", "reachable": False,
                        "error": "URL points to a private/loopback address"}
    except (socket.gaierror, ValueError):
        pass  # Let the request proceed; DNS resolution will fail naturally

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{url.rstrip('/')}/health") as resp:
                return {
                    "name": name,
                    "status": "ok" if resp.status < 500 else "error",
                    "reachable": resp.status < 500,
                    "http_status": resp.status,
                }
    except Exception as e:  # noqa: BLE001
        return {"name": name, "status": "unreachable", "reachable": False, "error": str(e)}


# ── Usage / billing dashboard ────────────────────────────────────────────
@router.get("/usage")
async def get_usage(
    period: str = Query("month", pattern="^(month|week)$"),
    breakdown: str = Query("profile", pattern="^(user|profile|model)$"),
    db: AsyncSession = Depends(get_db),
):
    """Token usage breakdown for the billing dashboard.

    Aggregates Message.tokens_in + tokens_out by user / profile / model,
    with a daily trend for the selected period.
    """
    from datetime import datetime, timedelta, timezone
    from app.db.models.conversation import Message
    from app.db.models.agent import Profile
    from app.services import settings_service

    now = datetime.now(tz=timezone.utc)
    if period == "week":
        since = now - timedelta(days=7)
    else:
        since = now - timedelta(days=30)

    # Daily trend
    daily_rows = (
        await db.execute(
            select(
                func.date_trunc("day", Message.created_at).label("date"),
                func.sum(Message.tokens_in).label("tin"),
                func.sum(Message.tokens_out).label("tout"),
                func.count(Message.id).label("cnt"),
            )
            .where(Message.created_at >= since, Message.role == "agent")
            .group_by("date")
            .order_by("date")
        )
    ).all()
    daily = [
        {"date": str(r.date), "tokens_in": int(r.tin or 0), "tokens_out": int(r.tout or 0), "count": int(r.cnt)}
        for r in daily_rows
    ]

    # Breakdown by dimension
    if breakdown == "user":
        dim_rows = (
            await db.execute(
                select(
                    Message.owner_id.label("key"),
                    func.sum(Message.tokens_in).label("tin"),
                    func.sum(Message.tokens_out).label("tout"),
                    func.count(Message.id).label("cnt"),
                )
                .where(Message.created_at >= since, Message.role == "agent")
                .group_by("key")
                .order_by(func.sum(Message.tokens_in + Message.tokens_out).desc())
            )
        ).all()
        # Resolve user names
        from app.db.models.user import User
        user_ids = [r.key for r in dim_rows if r.key]
        users_map = {}
        if user_ids:
            users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
            users_map = {u.id: u.name for u in users}
        by_dimension = [
            {
                "key": users_map.get(r.key, str(r.key)[:8]) if r.key else "unknown",
                "tokens_in": int(r.tin or 0),
                "tokens_out": int(r.tout or 0),
                "count": int(r.cnt),
            }
            for r in dim_rows
        ]
    elif breakdown == "model":
        dim_rows = (
            await db.execute(
                select(
                    Profile.default_model.label("key"),
                    func.sum(Message.tokens_in).label("tin"),
                    func.sum(Message.tokens_out).label("tout"),
                    func.count(Message.id).label("cnt"),
                )
                .select_from(Message)
                .outerjoin(Profile, Message.profile_id == Profile.id)
                .where(Message.created_at >= since, Message.role == "agent")
                .group_by("key")
                .order_by(func.sum(Message.tokens_in + Message.tokens_out).desc())
            )
        ).all()
        by_dimension = [
            {
                "key": r.key or "unknown",
                "tokens_in": int(r.tin or 0),
                "tokens_out": int(r.tout or 0),
                "count": int(r.cnt),
            }
            for r in dim_rows
        ]
    else:  # profile
        dim_rows = (
            await db.execute(
                select(
                    Message.profile_id.label("key"),
                    func.sum(Message.tokens_in).label("tin"),
                    func.sum(Message.tokens_out).label("tout"),
                    func.count(Message.id).label("cnt"),
                )
                .where(Message.created_at >= since, Message.role == "agent")
                .group_by("key")
                .order_by(func.sum(Message.tokens_in + Message.tokens_out).desc())
            )
        ).all()
        # Resolve profile names
        profile_ids = [r.key for r in dim_rows if r.key]
        profiles_map = {}
        if profile_ids:
            profiles = (await db.execute(select(Profile).where(Profile.id.in_(profile_ids)))).scalars().all()
            profiles_map = {p.id: p.name for p in profiles}
        by_dimension = [
            {
                "key": profiles_map.get(r.key, "未绑定") if r.key else "未绑定",
                "tokens_in": int(r.tin or 0),
                "tokens_out": int(r.tout or 0),
                "count": int(r.cnt),
            }
            for r in dim_rows
        ]

    # Model pricing for cost calculation
    settings = await settings_service.get(db)
    pricing = (settings.data or {}).get("model_pricing", {})
    # For cost: match by_dimension key against pricing keys (model breakdown uses model directly;
    # for profile/user, we don't have model-level cost without an extra join, so cost is 0 for those).
    def calc_cost(key: str, tin: int, tout: int) -> float:
        p = pricing.get(key)
        if not p:
            return 0.0
        return (tin / 1000) * p.get("input_per_1k", 0) + (tout / 1000) * p.get("output_per_1k", 0)

    for item in by_dimension:
        item["cost"] = round(calc_cost(item["key"], item["tokens_in"], item["tokens_out"]), 4)

    total_in = sum(d["tokens_in"] for d in daily)
    total_out = sum(d["tokens_out"] for d in daily)
    total_cost = sum(item["cost"] for item in by_dimension)

    return {
        "period": period,
        "breakdown": breakdown,
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
        "total_cost": round(total_cost, 4),
        "daily": daily,
        "by_dimension": by_dimension,
    }
