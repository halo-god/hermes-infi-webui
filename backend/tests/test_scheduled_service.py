"""scheduled_service unit tests — tick() due/stale/enabled logic, cron
computation and the scheduler lock. The API surface is covered by
test_scheduled_api.py; this exercises the service internals directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models.scheduled import ScheduledTask
from app.db.models.user import User
from app.services import scheduled_service as svc
from app.core.security import hash_password

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email: str, active: bool = True) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=active, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_task(db, owner: User, *, enabled=True, due=True, cron="0 9 * * *",
                   status=None) -> ScheduledTask:
    t = ScheduledTask(
        owner_id=owner.id, name="任务", agent_id="hermes", prompt="干活",
        cron=cron, enabled=enabled,
        next_run_at=(datetime.now(timezone.utc) - timedelta(minutes=5)) if due else None,
    )
    if status:
        t.last_status = status
        t.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.add(t)
    await db.flush()
    return t


async def test_compute_next_run_daily(db):
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    base = dt(2026, 8, 13, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    nxt = svc.compute_next_run("0 9 * * *", base)
    # 09:00 Asia/Shanghai == 01:00 UTC, next day
    assert nxt.day == 14 and nxt.hour == 1 and nxt.minute == 0


async def test_compute_next_run_invalid_raises(db):
    with pytest.raises(ValueError):
        svc.compute_next_run("bogus")


async def test_tick_triggers_due_task(client, db, monkeypatch):
    """A due enabled task is enqueued and rescheduled."""
    from app.core import redis as redis_core
    owner = await _mk_user(db, "tick1@h.io")
    task = await _mk_task(db, owner)

    enqueued: list[dict] = []
    async def _fake_enqueue(payload: dict) -> str:
        enqueued.append(payload)
        return "1-0"
    monkeypatch.setattr(redis_core, "enqueue_prompt", _fake_enqueue)

    n = await svc.tick(db)
    assert n == 1
    assert len(enqueued) == 1
    assert enqueued[0]["type"] == "scheduled"
    assert enqueued[0]["scheduled_task_id"] == str(task.id)
    assert task.last_status == "running"
    assert task.next_run_at is not None and task.next_run_at > datetime.now(timezone.utc)


async def test_tick_skips_disabled_and_future_tasks(db, monkeypatch):
    from app.core import redis as redis_core
    owner = await _mk_user(db, "tick2@h.io")
    await _mk_task(db, owner, enabled=False)
    future = ScheduledTask(
        owner_id=owner.id, name="未来任务", agent_id="hermes", prompt="x",
        cron="0 9 * * *", enabled=True,
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=5),
    )
    db.add(future)
    await db.flush()

    monkeypatch.setattr(redis_core, "enqueue_prompt", _fake_enqueue)
    n = await svc.tick(db)
    assert n == 0


async def _fake_enqueue(payload):
    return "1"


async def test_tick_skips_deactivated_users(db, monkeypatch):
    from app.core import redis as redis_core
    owner = await _mk_user(db, "tick3@h.io", active=False)
    await _mk_task(db, owner)
    monkeypatch.setattr(redis_core, "enqueue_prompt", _fake_enqueue)
    n = await svc.tick(db)
    assert n == 0


async def test_tick_reclaims_stale_running_task(db, monkeypatch):
    from app.core import redis as redis_core
    owner = await _mk_user(db, "tick4@h.io")
    stale = await _mk_task(db, owner, status="running", cron="0 9 * * *")
    # Make it look ancient so the stale cutoff catches it. Setting the attr
    # then flushing would re-trigger the onupdate timestamp — use a raw UPDATE.
    from sqlalchemy import update as _upd
    await db.execute(
        _upd(ScheduledTask).where(ScheduledTask.id == stale.id).values(
            updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
    )
    await db.commit()
    await db.refresh(stale)
    monkeypatch.setattr(redis_core, "enqueue_prompt", _fake_enqueue)
    await svc.tick(db)
    assert stale.last_status == "failed"
    assert (stale.fail_count or 0) >= 1
    # Core reclaim behavior: next_run_at must be pushed forward so the same
    # tick's due query cannot re-select this just-reclaimed task (regression:
    # reclaim without the push would re-fire it immediately).
    assert stale.next_run_at > datetime.now(timezone.utc)


async def test_tick_enqueue_failure_marks_failed(db, monkeypatch):
    from app.core import redis as redis_core
    owner = await _mk_user(db, "tick5@h.io")
    task = await _mk_task(db, owner)

    async def _boom(payload):
        raise RuntimeError("redis down")
    monkeypatch.setattr(redis_core, "enqueue_prompt", _boom)

    await svc.tick(db)
    assert task.last_status == "failed"


async def test_scheduler_loop_lock_serializes(client, db, monkeypatch):
    """scheduler_loop must skip tick when another worker holds the lock."""
    from app.core.redis import get_redis

    calls = {"n": 0}
    async def _tick(db):
        calls["n"] += 1
        return 0
    monkeypatch.setattr(svc, "tick", _tick)
    monkeypatch.setattr(svc, "TICK_INTERVAL", 3600)

    # Pre-hold the lock → loop must not tick
    await get_redis().set("hermes:scheduler:tick", "someone-else", nx=True, ex=60)
    try:
        await _run_loop_once(svc.scheduler_loop, monkeypatch)
    finally:
        await get_redis().delete("hermes:scheduler:tick")
    assert calls["n"] == 0, "tick must not run while lock is held"


async def _run_loop_once(loop_fn, monkeypatch):
    """Run one iteration of scheduler_loop then break via CancelledError."""
    import asyncio as _a
    async def _sleep(_s):
        raise _a.CancelledError()
    monkeypatch.setattr(_a, "sleep", _sleep)
    try:
        await loop_fn()
    except _a.CancelledError:
        pass
