"""FastAPI dependency guards for RBAC and team permissions."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.core.metrics import AUTH_FAILURES
from app.core.rbac import has_at_least
from app.deps import get_current_user
from app.db.models.user import User
from app.services import audit_service


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
