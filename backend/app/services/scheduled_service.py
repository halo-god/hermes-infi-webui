"""Scheduled task service — CRUD + cron scheduling + tick loop.

The tick loop runs in the FastAPI lifespan: every 60s it queries for due
tasks and enqueues them onto the runner's Redis Stream (same path as chat
turns and memory consolidation).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import redis as redis_core
from app.db.models.scheduled import ScheduledTask
from app.db.models.user import User
from app.schemas.scheduled import ScheduledTaskCreate, ScheduledTaskUpdate

logger = logging.getLogger(__name__)


def _get_tz():
    """Get the configured scheduler timezone. Falls back to UTC if tzdata
    is missing or the configured name is invalid."""
    try:
        return ZoneInfo(settings.scheduler_timezone)
    except Exception:
        logger.warning(
            "ZoneInfo('%s') failed, falling back to UTC", settings.scheduler_timezone,
        )
        return timezone.utc

#: Seconds between scheduler ticks.
TICK_INTERVAL = 60


def compute_next_run(cron_expr: str, from_time: datetime | None = None) -> datetime:
    """Compute the next trigger time for a cron expression.

    Raises ValueError if the expression is invalid.
    """
    # Use the configured timezone as base so the cron expression's hour/minute
    # match the user's local time selection (e.g. "16:08" = 16:08 local).
    tz = _get_tz()
    base = from_time or datetime.now(tz)
    cron = croniter(cron_expr, base)
    nxt = cron.get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=tz)
    return nxt.astimezone(timezone.utc)


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


#: Maximum scheduled tasks per user (prevents amplification DoS).
MAX_TASKS_PER_USER = 20
#: Maximum tasks to enqueue per tick cycle.
MAX_ENQUEUE_PER_TICK = 50


async def create_task(db: AsyncSession, owner_id, payload: ScheduledTaskCreate) -> ScheduledTask:
    # Enforce per-user quota to prevent scheduler amplification attacks.
    count = (
        await db.execute(
            select(func.count()).select_from(ScheduledTask).where(
                ScheduledTask.owner_id == owner_id
            )
        )
    ).scalar() or 0
    if count >= MAX_TASKS_PER_USER:
        raise ValueError(f"已达到每用户 {MAX_TASKS_PER_USER} 个定时任务上限")

    # Validate cron early (raises ValueError -> 400 in route).
    next_run = compute_next_run(payload.cron) if payload.enabled else None
    task = ScheduledTask(
        owner_id=owner_id,
        name=payload.name,
        agent_id=payload.agent_id,
        profile_id=payload.profile_id,
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
    """Find due tasks and enqueue them. Returns count of triggered tasks.

    Bounded by MAX_ENQUEUE_PER_TICK to prevent a single tick from
    overwhelming the runner when many tasks are due simultaneously.
    """
    now = datetime.now(timezone.utc)
    # Bug 2 fix: compute_next_run uses local-tz base for cron matching, but
    # tick passes UTC. Convert to local-tz-aware before computing next_run
    # so croniter matches the same hour/minute the user configured.
    tz_base = now.astimezone(_get_tz())

    # S1: reclaim tasks stuck in "running" — a crashed runner leaves them
    # looking perpetually in-flight. Anything older than 2× the prompt
    # timeout can't still be executing (single turn is capped at
    # acp_prompt_timeout), so mark it failed and let the next tick fire.
    stale_cutoff = now - timedelta(seconds=settings.acp_prompt_timeout * 2)
    stale = (
        await db.execute(
            select(ScheduledTask).where(
                ScheduledTask.enabled.is_(True),
                ScheduledTask.last_status == "running",
                ScheduledTask.updated_at < stale_cutoff,
            )
        )
    ).scalars().all()
    for t in stale:
        logger.warning(
            "Reclaiming stale running task %s (updated %s)", t.id, t.updated_at,
        )
        t.last_status = "failed"
        t.fail_count = (t.fail_count or 0) + 1

    due = (
        await db.execute(
            select(ScheduledTask)
            # Never trigger tasks of deactivated users — an admin-disable must
            # stop their agents from firing (cost + unwanted notifications).
            .join(User, ScheduledTask.owner_id == User.id)
            .where(
                ScheduledTask.enabled.is_(True),
                User.is_active.is_(True),
                ScheduledTask.next_run_at <= now,
            ).limit(MAX_ENQUEUE_PER_TICK)
        )
    ).scalars().all()

    count = 0
    for task in due:
        try:
            await redis_core.enqueue_prompt({
                "type": "scheduled",
                "user_id": str(task.owner_id),
                "agent_id": task.agent_id,
                "profile_id": str(task.profile_id) if task.profile_id else None,
                "prompt": task.prompt,
                "scheduled_task_id": str(task.id),
            })
            task.last_run_at = now
            task.last_status = "running"
            task.next_run_at = compute_next_run(task.cron, tz_base)
            count += 1
        except Exception:
            logger.exception("Failed to enqueue scheduled task %s", task.id)
            task.last_status = "failed"
            task.next_run_at = compute_next_run(task.cron, tz_base)

    # Commit BOTH the due-enqueue updates AND any stale-reclaim changes —
    # previously the stale branch only landed when a due task existed in the
    # same tick, so a lone stale task stayed "running" forever.
    if due or stale:
        await db.commit()
    return count


async def scheduler_loop():
    """Background loop — call tick() every TICK_INTERVAL seconds.

    Started in main.py lifespan; cancelled on shutdown. A Redis NX lock
    (TTL > interval) serializes tick across API workers: without it, N
    uvicorn workers each select + enqueue the same due tasks — duplicate
    firings, and stale-reclaim would flag the other worker's in-flight
    task as stuck.
    """
    from app.db.base import async_session_maker
    logger.info("Scheduled task loop started (interval=%ss)", TICK_INTERVAL)
    lock_key = "hermes:scheduler:tick"
    while True:
        try:
            got = await redis_core.get_redis().set(
                lock_key, "1", nx=True, ex=TICK_INTERVAL + 5,
            )
            if got:
                try:
                    async with async_session_maker() as db:
                        n = await tick(db)
                        if n:
                            logger.info("Scheduler triggered %d task(s)", n)
                finally:
                    try:
                        await redis_core.get_redis().delete(lock_key)
                    except Exception:
                        pass
            else:
                logger.debug("Scheduler tick skipped — another worker holds the lock")
        except asyncio.CancelledError:
            logger.info("Scheduled task loop cancelled")
            raise
        except Exception:
            logger.exception("Scheduler tick error")
        await asyncio.sleep(TICK_INTERVAL)
