"""FastAPI dependency guards for RBAC and team permissions."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.core.rbac import has_at_least
from app.deps import get_current_user
from app.db.models.user import User


def require_role(required: str):
    """Dependency factory: ensures the current user has >= required role."""

    async def _guard(user: User = Depends(get_current_user)) -> User:
        if not has_at_least(user.role, required):
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
