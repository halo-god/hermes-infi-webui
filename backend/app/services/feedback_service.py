"""Feedback service — CRUD + admin reply workflow."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feedback import Feedback
from app.schemas.feedback import FeedbackCreate, FeedbackUpdate


async def list_feedback(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    is_admin: bool = False,
    status: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[Feedback]:
    """List feedback. Admins see all; regular users see only their own."""
    stmt = select(Feedback)
    if not is_admin:
        stmt = stmt.where(Feedback.user_id == user_id)
    if status:
        stmt = stmt.where(Feedback.status == status)
    if category:
        stmt = stmt.where(Feedback.category == category)
    stmt = stmt.order_by(Feedback.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_feedback(
    db: AsyncSession, feedback_id: int, user_id: uuid.UUID | None = None, is_admin: bool = False
) -> Feedback | None:
    stmt = select(Feedback).where(Feedback.id == feedback_id)
    if not is_admin:
        stmt = stmt.where(Feedback.user_id == user_id)
    return (await db.execute(stmt)).scalars().first()


async def create_feedback(
    db: AsyncSession, user_id: uuid.UUID, user_name: str, payload: FeedbackCreate
) -> Feedback:
    fb = Feedback(
        user_id=user_id,
        user_name=user_name,
        title=payload.title,
        content=payload.content,
        category=payload.category,
        images=list(payload.images) if payload.images else [],
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)
    return fb


async def update_feedback(
    db: AsyncSession, feedback_id: int, admin_id: uuid.UUID, payload: FeedbackUpdate
) -> Feedback | None:
    fb = await db.get(Feedback, feedback_id)
    if not fb:
        return None
    changed = False
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(fb, field, value)
        changed = True
    if changed:
        fb.replied_by = admin_id
        fb.replied_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(fb)
    return fb
