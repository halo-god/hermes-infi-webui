"""Feedback CRUD — users submit feedback, admins reply and manage status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.guards import require_admin
from app.core.rbac import has_at_least
from app.db.base import get_db
from app.db.models.user import User
from app.deps import get_current_user
from app.schemas.feedback import FeedbackCreate, FeedbackOut, FeedbackUpdate
from app.services import feedback_service as svc

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("", response_model=list[FeedbackOut])
async def list_feedback(
    status: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List feedback. Admins see all; regular users see only their own."""
    is_admin = has_at_least(user.role, "admin")
    return await svc.list_feedback(db, user_id=user.id, is_admin=is_admin, status=status, category=category, limit=limit)


@router.post("", response_model=FeedbackOut, status_code=201)
async def create_feedback(
    payload: FeedbackCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await svc.create_feedback(db, user.id, user.name, payload)


@router.get("/{feedback_id}", response_model=FeedbackOut)
async def get_feedback(
    feedback_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    is_admin = has_at_least(user.role, "admin")
    fb = await svc.get_feedback(db, feedback_id, user_id=user.id, is_admin=is_admin)
    if not fb:
        raise HTTPException(status_code=404, detail="反馈不存在")
    return fb


@router.patch("/{feedback_id}", response_model=FeedbackOut)
async def update_feedback(
    feedback_id: int,
    payload: FeedbackUpdate,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: update status, priority, and/or reply."""
    fb = await svc.update_feedback(db, feedback_id, admin.id, payload)
    if not fb:
        raise HTTPException(status_code=404, detail="反馈不存在")
    return fb
