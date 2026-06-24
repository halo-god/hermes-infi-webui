"""Scheduled tasks CRUD — personal recurring ACP agent tasks."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models.user import User
from app.core.guards import require_permission
from app.schemas.scheduled import ScheduledTaskCreate, ScheduledTaskOut, ScheduledTaskUpdate
from app.services import scheduled_service as svc

router = APIRouter(prefix="/scheduled", tags=["scheduled"])


@router.get("", response_model=list[ScheduledTaskOut])
async def list_tasks(
    user: User = Depends(require_permission("scheduled.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await svc.list_tasks(db, user.id)


@router.post("", response_model=ScheduledTaskOut, status_code=201)
async def create_task(
    payload: ScheduledTaskCreate,
    user: User = Depends(require_permission("scheduled.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await svc.create_task(db, user.id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"无效的 cron 表达式: {e}")


@router.patch("/{task_id}", response_model=ScheduledTaskOut)
async def update_task(
    task_id: uuid.UUID,
    payload: ScheduledTaskUpdate,
    user: User = Depends(require_permission("scheduled.manage")),
    db: AsyncSession = Depends(get_db),
):
    try:
        task = await svc.update_task(db, task_id, user.id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"无效的 cron 表达式: {e}")
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID,
    user: User = Depends(require_permission("scheduled.manage")),
    db: AsyncSession = Depends(get_db),
):
    ok = await svc.delete_task(db, task_id, user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")


@router.post("/{task_id}/toggle", response_model=ScheduledTaskOut)
async def toggle_task(
    task_id: uuid.UUID,
    enabled: bool = True,
    user: User = Depends(require_permission("scheduled.manage")),
    db: AsyncSession = Depends(get_db),
):
    task = await svc.toggle_task(db, task_id, user.id, enabled)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task
