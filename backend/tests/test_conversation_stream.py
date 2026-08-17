"""End-to-end conversation streaming test (P2).

Runs the Agent Runner in-process (background task), drives the API via
httpx ASGITransport, and collects streamed events off the per-conversation
Redis Stream — proving: send → Redis Stream → ACP subprocess (mock) →
evt:conv:{id} stream → tokens + tool + produced file → persisted message +
workspace file. Reading from id 0 also proves replay: no subscribe-before-
publish ordering is required.

Requires reachable PostgreSQL + Redis and a seeded admin.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.main import app
from agent_runner.discovery import find_hermes_binary
from agent_runner.runner import Runner

PREFIX = settings.api_v1_prefix


async def _services_ok() -> bool:
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        await R.get_redis().ping()
        return True
    except Exception:
        return False


async def collect_events(cid: str, timeout_s: float) -> list[dict]:
    """Read events from the conversation stream (from the beginning) until 'done'."""
    events: list[dict] = []
    last_id = "0-0"
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        entries = await R.read_events(cid, last_id, block_ms=3000)
        for entry_id, data in entries:
            last_id = entry_id
            ev = json.loads(data)
            events.append(ev)
            if ev.get("type") == "done":
                return events
    return events


@pytest.mark.asyncio
async def test_conversation_stream_e2e():
    if not await _services_ok():
        pytest.skip("PostgreSQL/Redis not reachable — start services + migrate + seed")
    if not find_hermes_binary():
        # CI has no hermes ACP binary — this test drives a real agent and can
        # only run where the Hermes Agent CLI is installed.
        pytest.skip("hermes ACP binary not found — install hermes-agent to run")

    runner = Runner()
    runner_task = asyncio.create_task(runner.run())
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # login
            r = await c.post(
                f"{PREFIX}/auth/login",
                json={
                    "method": "local",
                    "username": settings.first_admin_email,
                    "password": settings.first_admin_password,
                },
            )
            assert r.status_code == 200, r.text
            h = {"Authorization": f"Bearer {r.json()['access_token']}"}

            # create conversation
            r = await c.post(
                f"{PREFIX}/conversations", json={"primary_agent_id": "hermes"}, headers=h
            )
            assert r.status_code == 201, r.text
            cid = r.json()["id"]

            # send — no need to subscribe first: the event stream replays
            r = await c.post(
                f"{PREFIX}/conversations/{cid}/messages",
                json={"text": "帮我写一个项目启动会的会议纪要"},
                headers=h,
            )
            assert r.status_code == 200, r.text

            events = await collect_events(cid, 90)

            types = [e["type"] for e in events]
            assert "token" in types, types
            assert "file" in types, types
            assert any(e["type"] == "done" and e.get("status") == "complete" for e in events)

            # persisted agent message
            r = await c.get(f"{PREFIX}/conversations/{cid}", headers=h)
            agent_msgs = [m for m in r.json()["messages"] if m["role"] == "agent"]
            assert agent_msgs[-1]["status"] == "complete"
            assert agent_msgs[-1]["content"]["text"]

            # persisted workspace file
            r = await c.get(f"{PREFIX}/conversations/{cid}/files", headers=h)
            files = r.json()
            assert files and files[0]["name"]
    finally:
        runner_task.cancel()
        try:
            await runner_task
        except asyncio.CancelledError:
            pass
        await runner.pool.close_all()
