"""Scheduled tasks API — CRUD + toggle + cron validation + permission gate.

Previously zero coverage for the 5 endpoints (list/create/update/delete/
toggle). Uses the transaction-rolled-back client fixture, so no real tasks
are created in the dev DB.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


PREFIX = "/api/v1/scheduled"


async def _mk_task(client, headers, **overrides) -> dict:
    payload = {
        "name": "测试任务",
        "agent_id": "hermes",
        "prompt": "生成日报",
        "cron": "0 9 * * *",
        "enabled": True,
        **overrides,
    }
    r = await client.post(PREFIX, json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_task_computes_next_run(client, auth_headers):
    """Creating an enabled daily-09:00 task must schedule tomorrow 09:00 local."""
    task = await _mk_task(client, auth_headers)
    assert task["name"] == "测试任务"
    assert task["cron"] == "0 9 * * *"
    assert task["enabled"] is True
    # next_run_at: next day at 09:00 Asia/Shanghai, stored as UTC
    assert task["next_run_at"] is not None
    from datetime import datetime
    nxt = datetime.fromisoformat(task["next_run_at"].replace("Z", "+00:00"))
    assert nxt.hour == 1 and nxt.minute == 0, f"09:00 CST == 01:00 UTC, got {nxt}"


async def test_create_task_with_disabled_flag_has_no_next_run(client, auth_headers):
    task = await _mk_task(client, auth_headers, enabled=False)
    assert task["enabled"] is False
    assert task["next_run_at"] is None


async def test_create_task_rejects_invalid_cron(client, auth_headers):
    r = await client.post(PREFIX, json={
        "name": "坏任务", "agent_id": "hermes", "prompt": "x", "cron": "not-a-cron",
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "cron" in r.json()["detail"].lower()


async def test_create_task_enforces_quota(client, auth_headers, db):
    """MAX_TASKS_PER_USER is a scheduler-amplification guard."""
    from app.services.scheduled_service import MAX_TASKS_PER_USER
    for i in range(MAX_TASKS_PER_USER):
        await _mk_task(client, auth_headers, name=f"配额任务{i}", cron=f"{i % 60} * * * *")
    r = await client.post(PREFIX, json={
        "name": "超额任务", "agent_id": "hermes", "prompt": "x", "cron": "0 9 * * *",
    }, headers=auth_headers)
    assert r.status_code == 400
    assert "上限" in r.json()["detail"]


async def test_list_tasks_returns_own_only(client, auth_headers, db):
    """A user's list must not leak other users' tasks."""
    from app.core.security import create_token, hash_password
    from app.db.models.user import User

    other = User(
        id=uuid.uuid4(), email="sched-other@h.io", name="other",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(other)
    await db.flush()
    other_headers = {"Authorization": f"Bearer {create_token(str(other.id), 'access')[0]}"}
    await _mk_task(client, other_headers, name="别人的任务")

    await _mk_task(client, auth_headers, name="我的任务")
    r = await client.get(PREFIX, headers=auth_headers)
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "我的任务" in names
    assert "别人的任务" not in names, "cross-user task leaked"


async def test_update_task_renames_and_reschedules(client, auth_headers):
    task = await _mk_task(client, auth_headers, cron="0 9 * * *")
    r = await client.patch(f"{PREFIX}/{task['id']}", json={
        "name": "改名任务", "cron": "30 8 * * *",
    }, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["name"] == "改名任务"
    assert r.json()["cron"] == "30 8 * * *"
    # 08:30 CST == 00:30 UTC
    from datetime import datetime
    nxt = datetime.fromisoformat(r.json()["next_run_at"].replace("Z", "+00:00"))
    assert nxt.hour == 0 and nxt.minute == 30


async def test_update_task_invalid_cron_400(client, auth_headers):
    task = await _mk_task(client, auth_headers)
    r = await client.patch(f"{PREFIX}/{task['id']}", json={"cron": "bogus"},
                           headers=auth_headers)
    assert r.status_code == 400


async def test_update_missing_task_404(client, auth_headers):
    r = await client.patch(f"{PREFIX}/{uuid.uuid4()}", json={"name": "x"},
                           headers=auth_headers)
    assert r.status_code == 404


async def test_toggle_pause_and_resume(client, auth_headers):
    task = await _mk_task(client, auth_headers)
    r = await client.post(f"{PREFIX}/{task['id']}/toggle?enabled=false", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["next_run_at"] is None
    r = await client.post(f"{PREFIX}/{task['id']}/toggle?enabled=true", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["enabled"] is True
    assert r.json()["next_run_at"] is not None


async def test_delete_task(client, auth_headers):
    task = await _mk_task(client, auth_headers)
    r = await client.delete(f"{PREFIX}/{task['id']}", headers=auth_headers)
    assert r.status_code == 204
    r = await client.get(PREFIX, headers=auth_headers)
    assert all(t["id"] != task["id"] for t in r.json())


async def test_delete_missing_task_404(client, auth_headers):
    r = await client.delete(f"{PREFIX}/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
