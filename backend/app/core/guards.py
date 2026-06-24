"""FastAPI dependency guards for RBAC and team permissions."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import AUTH_FAILURES
from app.core.rbac import PERMISSION_CATALOG, has_at_least
from app.db.base import get_db
from app.deps import get_current_user
from app.db.models.user import User
from app.services import audit_service, settings_service


def _default_roles(perm_id: str) -> list[str]:
    """Return the catalog default roles for a permission id."""
    for group in PERMISSION_CATALOG:
        for item in group["items"]:
            if item["id"] == perm_id:
                return list(item["roles"])
    return []


def require_role(required: str):
    """Dependency factory: ensures the current user has >= required role."""

    async def _guard(request: Request, user: User = Depends(get_current_user)) -> User:
        if not has_at_least(user.role, required):
            # Leave a trail: a burst of authz.deny is an escalation-probe signal.
            AUTH_FAILURES.labels("authz_deny").inc()
            await audit_service.record(
                action="authz.deny",
                actor_id=user.id,
                actor_name=user.name,
                target=request.url.path,
                ip=request.client.host if request.client else None,
                result="deny",
                meta={"required_role": required, "role": user.role},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足",
            )
        return user

    return _guard


def require_admin():
    """Dependency: requires admin or super_admin role."""
    return require_role("admin")


def require_super_admin():
    """Dependency: requires super_admin role."""
    return require_role("super_admin")


def require_permission(perm_id: str):
    """Dependency factory: checks the platform permission matrix.

    super_admin always passes. For other roles, consults the
    `permission_overrides` in system_settings (the admin-toggleable matrix),
    falling back to the catalog defaults when no override exists.
    """

    async def _guard(
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if has_at_least(user.role, "super_admin"):
            return user
        s = await settings_service.get(db)
        overrides: dict = (s.data or {}).get("permission_overrides", {})
        roles = overrides.get(perm_id) or _default_roles(perm_id)
        if user.role not in roles:
            AUTH_FAILURES.labels("authz_deny").inc()
            await audit_service.record(
                action="authz.deny",
                actor_id=user.id,
                actor_name=user.name,
                target=request.url.path,
                ip=request.client.host if request.client else None,
                result="deny",
                meta={"perm_id": perm_id, "role": user.role},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"无「{perm_id}」权限",
            )
        return user

    return _guard
