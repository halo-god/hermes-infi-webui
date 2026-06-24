"""Scheduled task service — CRUD + cron scheduling + tick loop.

The tick loop runs in the FastAPI lifespan: every 60s it queries for due
tasks and enqueues them onto the runner's Redis Stream (same path as chat
turns and memory consolidation).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_core
from app.db.models.scheduled import ScheduledTask
from app.schemas.scheduled import ScheduledTaskCreate, ScheduledTaskUpdate

logger = logging.getLogger(__name__)

#: Seconds between scheduler ticks.
TICK_INTERVAL = 60


def compute_next_run(cron_expr: str, from_time: datetime | None = None) -> datetime:
    """Compute the next trigger time for a cron expression.

    Raises ValueError if the expression is invalid.
    """
    base = from_time or datetime.now(timezone.utc)
    cron = croniter(cron_expr, base)
    nxt = cron.get_next(datetime)
    # croniter may strip tzinfo depending on input; ensure tz-aware UTC.
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    return nxt


async def list_tasks(db: AsyncSession, owner_id) -> list[ScheduledTask]:
    rows = (
        await db.execute(
            select(ScheduledTask)
            .where(ScheduledTask.owner_id == owner_id)
            .order_by(ScheduledTask.next_run_at.asc().nulls_last(), ScheduledTask.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def get_task(db: AsyncSession, task_id, owner_id) -> ScheduledTask | None:
    return (
        await db.execute(
            select(ScheduledTask).where(
                ScheduledTask.id == task_id,
                ScheduledTask.owner_id == owner_id,
            )
        )
    ).scalars().first()


async def create_task(db: AsyncSession, owner_id, payload: ScheduledTaskCreate) -> ScheduledTask:
    # Validate cron early (raises ValueError → 400 in route).
    next_run = compute_next_run(payload.cron) if payload.enabled else None
    task = ScheduledTask(
        owner_id=owner_id,
        name=payload.name,
        agent_id=payload.agent_id,
        prompt=payload.prompt,
        cron=payload.cron,
        enabled=payload.enabled,
        next_run_at=next_run,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def update_task(
    db: AsyncSession, task_id, owner_id, payload: ScheduledTaskUpdate
) -> ScheduledTask | None:
    task = await get_task(db, task_id, owner_id)
    if not task:
        return None
    changed = False
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
        changed = True
    if changed:
        # Recompute next_run_at if cron or enabled changed.
        if task.enabled:
            task.next_run_at = compute_next_run(task.cron)
        else:
            task.next_run_at = None
    await db.commit()
    await db.refresh(task)
    return task


async def delete_task(db: AsyncSession, task_id, owner_id) -> bool:
    task = await get_task(db, task_id, owner_id)
    if not task:
        return False
    await db.delete(task)
    await db.commit()
    return True


async def toggle_task(db: AsyncSession, task_id, owner_id, enabled: bool) -> ScheduledTask | None:
    task = await get_task(db, task_id, owner_id)
    if not task:
        return None
    task.enabled = enabled
    task.next_run_at = compute_next_run(task.cron) if enabled else None
    await db.commit()
    await db.refresh(task)
    return task


# ── scheduler tick ────────────────────────────────────────────────────
async def tick(db: AsyncSession) -> int:
    """Find due tasks and enqueue them. Returns count of triggered tasks."""
    now = datetime.now(timezone.utc)
    due = (
        await db.execute(
            select(ScheduledTask).where(
                ScheduledTask.enabled.is_(True),
                ScheduledTask.next_run_at <= now,
            )
        )
    ).scalars().all()

    count = 0
    for task in due:
        try:
            await redis_core.enqueue_prompt({
                "type": "scheduled",
                "user_id": str(task.owner_id),
                "agent_id": task.agent_id,
                "prompt": task.prompt,
                "scheduled_task_id": str(task.id),
            })
            task.last_run_at = now
            task.last_status = "running"
            task.next_run_at = compute_next_run(task.cron, now)
            count += 1
        except Exception:
            logger.exception("Failed to enqueue scheduled task %s", task.id)
            task.last_status = "failed"
            task.next_run_at = compute_next_run(task.cron, now)

    if due:
        await db.commit()
    return count


async def scheduler_loop():
    """Background loop — call tick() every TICK_INTERVAL seconds.

    Started in main.py lifespan; cancelled on shutdown.
    """
    from app.db.base import async_session_maker
    logger.info("Scheduled task loop started (interval=%ss)", TICK_INTERVAL)
    while True:
        try:
            async with async_session_maker() as db:
                n = await tick(db)
                if n:
                    logger.info("Scheduler triggered %d task(s)", n)
        except asyncio.CancelledError:
            logger.info("Scheduled task loop cancelled")
            raise
        except Exception:
            logger.exception("Scheduler tick error")
        await asyncio.sleep(TICK_INTERVAL)
