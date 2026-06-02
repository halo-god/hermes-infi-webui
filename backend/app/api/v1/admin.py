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
from app.db.models.agent import Agent
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
        agents=await count(Agent),
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
async def list_mappings(pid: str, db: AsyncSession = Depends(get_db)):
    return await identity_service.list_mappings(db, pid)


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
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Test connectivity and credential validity for an identity provider."""
    return await identity_service.test_provider(db, pid)
