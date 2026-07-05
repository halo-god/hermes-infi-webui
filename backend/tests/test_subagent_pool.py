"""Background subagents: pool lifecycle (idle/max-lifetime eviction),
spawn/send execution, and restart reconciliation.

Uses a FakeACPClient monkeypatched into agent_runner.acp_persona (where
make_persona_client actually constructs ACPClient), same style as
test_session_pool_profile.py's pool-level fixture.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from agent_runner import acp_persona, runner_subagent as rs
from agent_runner.subagent_pool import SubagentPool
from app.core.security import hash_password
from app.db.base import async_session_maker
from app.db.models.conversation import Conversation, Message
from app.db.models.subagent import BackgroundSubagent
from app.db.models.user import User


# ── Pure pool tests (no DB/Redis) ───────────────────────────────────────────

class FakeProc:
    returncode = None


class FakeClient:
    def __init__(self):
        self._closed = False
        self._proc = FakeProc()
        self.stopped = False

    async def stop(self):
        self.stopped = True
        self._closed = True


@pytest.fixture
def pool() -> SubagentPool:
    return SubagentPool()


async def test_register_and_get(pool):
    c = FakeClient()
    pool.register("s1", c, idle_timeout=900, max_lifetime=14400)
    assert pool.get("s1") is c


async def test_get_returns_none_for_dead_client(pool):
    c = FakeClient()
    c._proc.returncode = 1
    pool.register("s1", c, idle_timeout=900, max_lifetime=14400)
    assert pool.get("s1") is None


async def test_idle_eviction(pool):
    c = FakeClient()
    pool.register("s1", c, idle_timeout=900, max_lifetime=14400)
    pool._last_active["s1"] -= 1000  # simulate 1000s of inactivity
    expired = await pool.evict_expired()
    assert expired == [("s1", "idle")]
    assert c.stopped
    assert pool.get("s1") is None


async def test_max_lifetime_eviction_even_if_active(pool):
    c = FakeClient()
    pool.register("s1", c, idle_timeout=900, max_lifetime=14400)
    pool._spawned_at["s1"] -= 20000  # older than max_lifetime
    pool._last_active["s1"] = pool._last_active["s1"]  # still "active"
    expired = await pool.evict_expired()
    assert expired == [("s1", "max_lifetime")]
    assert c.stopped


async def test_touch_resets_idle_clock(pool):
    c = FakeClient()
    pool.register("s1", c, idle_timeout=900, max_lifetime=14400)
    pool._last_active["s1"] -= 1000
    pool.touch("s1")
    assert pool.expiry_reason("s1") is None


async def test_close_all_stops_everything(pool):
    c1, c2 = FakeClient(), FakeClient()
    pool.register("s1", c1, idle_timeout=900, max_lifetime=14400)
    pool.register("s2", c2, idle_timeout=900, max_lifetime=14400)
    await pool.close_all()
    assert c1.stopped and c2.stopped
    assert pool.ids() == []


# ── Spawn/send execution (real DB, fake ACPClient, real Redis) ─────────────

class ScriptedACPClient:
    """Fake persona client: replies with a fixed chunk of text on prompt()."""
    instances: list["ScriptedACPClient"] = []

    def __init__(self, command, cwd, *, protocol_version=1, on_update=None, on_fs_write=None, env=None):
        self.command = command
        self.cwd = cwd
        self.on_update = on_update
        self.on_fs_write = on_fs_write
        self._closed = False
        self._proc = FakeProc()
        self._session_id = None
        self.stopped = False
        self.reply_text = "默认回复"
        ScriptedACPClient.instances.append(self)

    async def start(self):
        pass

    async def initialize(self):
        return {}

    async def new_session(self, cwd, mcp_servers=None):
        self._session_id = f"sess-{uuid.uuid4().hex[:8]}"
        return self._session_id

    async def prompt(self, text):
        if self.on_update:
            await self.on_update({"sessionUpdate": "agent_message_chunk", "content": {"text": self.reply_text}})
        return "end_turn"

    async def stop(self):
        self.stopped = True
        self._closed = True


@pytest.fixture
def fake_agents(monkeypatch):
    monkeypatch.setattr(acp_persona, "ACPClient", ScriptedACPClient)
    ScriptedACPClient.instances.clear()
    from types import SimpleNamespace
    return {"hermes": SimpleNamespace(command=["hermes", "acp"], label="Hermes", color="#000", description="")}


@pytest_asyncio.fixture
async def real_db():
    """A genuinely-committing session against the same DB runner_subagent.py's
    own async_session_maker() calls use.

    The shared `db` fixture wraps everything in an external transaction that
    SQLAlchemy turns into a SAVEPOINT (join_transaction_mode="conditional_savepoint")
    so it can roll back at teardown — but that means a *different* connection,
    like the ones handle_subagent_spawn/send open internally via their own
    async_session_maker(), can never see data staged through it. These tests
    exercise exactly that cross-session boundary, so they need a session that
    really commits. Cleanup is manual (delete by owner_id) instead of rollback.
    """
    session = async_session_maker()
    owner_ids: list[uuid.UUID] = []
    try:
        yield session, owner_ids
    finally:
        for uid in owner_ids:
            await session.execute(delete(User).where(User.id == uid))
        await session.commit()
        await session.close()


async def _mk_user(db, owner_ids: list[uuid.UUID], email: str) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    owner_ids.append(u.id)
    return u


async def _mk_subagent(db, owner: User) -> tuple[Conversation, BackgroundSubagent]:
    parent = Conversation(owner_id=owner.id, title="父会话", primary_agent_id="hermes")
    db.add(parent)
    await db.flush()
    subconv = Conversation(owner_id=owner.id, title="后台任务", type="subagent", primary_agent_id="hermes")
    db.add(subconv)
    await db.flush()
    row = BackgroundSubagent(
        parent_conversation_id=parent.id, subagent_conversation_id=subconv.id,
        owner_id=owner.id, purpose="调研一下", agent_id="hermes", status="starting",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return parent, row


async def test_handle_subagent_spawn_runs_initial_prompt(real_db, fake_agents):
    db, owner_ids = real_db
    owner = await _mk_user(db, owner_ids, "spawn-owner@h.io")
    _parent, row = await _mk_subagent(db, owner)
    pool = SubagentPool()

    ScriptedACPClient.instances.clear()
    await rs.handle_subagent_spawn(
        {"subagent_id": str(row.id), "agent_id": "hermes", "initial_prompt": "开始调研"},
        fake_agents, pool,
    )

    await db.refresh(row)
    assert row.status == "idle"  # turn complete, session alive for follow-ups
    assert pool.get(str(row.id)) is not None  # client stays registered

    msgs = list((await db.execute(
        select(Message).where(Message.conversation_id == row.subagent_conversation_id)
    )).scalars().all())
    assert len(msgs) == 1
    assert msgs[0].content["text"] == "默认回复"
    assert msgs[0].status == "complete"


async def test_handle_subagent_send_reuses_live_client(real_db, fake_agents):
    db, owner_ids = real_db
    owner = await _mk_user(db, owner_ids, "send-owner@h.io")
    _parent, row = await _mk_subagent(db, owner)
    pool = SubagentPool()

    await rs.handle_subagent_spawn(
        {"subagent_id": str(row.id), "agent_id": "hermes", "initial_prompt": "第一轮"},
        fake_agents, pool,
    )
    assert len(ScriptedACPClient.instances) == 1

    ScriptedACPClient.instances[0].reply_text = "第二轮回复"
    await rs.handle_subagent_send({"subagent_id": str(row.id), "text": "继续"}, fake_agents, pool)

    # No new subprocess spawned for the follow-up turn — same client reused.
    assert len(ScriptedACPClient.instances) == 1

    msgs = list((await db.execute(
        select(Message)
        .where(Message.conversation_id == row.subagent_conversation_id)
        .order_by(Message.created_at.asc())
    )).scalars().all())
    assert len(msgs) == 2
    assert msgs[1].content["text"] == "第二轮回复"


async def test_handle_subagent_send_after_eviction_errors_clearly(real_db, fake_agents):
    db, owner_ids = real_db
    owner = await _mk_user(db, owner_ids, "evicted-owner@h.io")
    _parent, row = await _mk_subagent(db, owner)
    pool = SubagentPool()

    await rs.handle_subagent_spawn(
        {"subagent_id": str(row.id), "agent_id": "hermes", "initial_prompt": "第一轮"},
        fake_agents, pool,
    )
    await pool.drop(str(row.id))  # simulate eviction/crash

    await rs.handle_subagent_send({"subagent_id": str(row.id), "text": "还在吗"}, fake_agents, pool)

    await db.refresh(row)
    assert row.status == "error"
    assert row.error_detail


# ── Restart reconciliation ──────────────────────────────────────────────────

async def test_reconcile_marks_live_rows_interrupted(real_db):
    db, owner_ids = real_db
    owner = await _mk_user(db, owner_ids, "reconcile-owner@h.io")
    _parent, row = await _mk_subagent(db, owner)
    row.status = "running"
    await db.commit()

    await rs.reconcile_background_subagents()

    await db.refresh(row)
    assert row.status == "interrupted"
    assert row.error_detail


async def test_reconcile_leaves_terminal_rows_alone(real_db):
    db, owner_ids = real_db
    owner = await _mk_user(db, owner_ids, "reconcile-owner2@h.io")
    _parent, row = await _mk_subagent(db, owner)
    row.status = "done"
    await db.commit()

    await rs.reconcile_background_subagents()

    await db.refresh(row)
    assert row.status == "done"


# ── API: hidden conversation must never leak into the sidebar list ─────────

async def test_spawned_subagent_conversation_excluded_from_list(client, auth_headers):
    r = await client.post("/api/v1/conversations", json={"primary_agent_id": "hermes"}, headers=auth_headers)
    assert r.status_code == 201
    parent_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/conversations/{parent_id}/subagents",
        json={"purpose": "调研一下", "initial_prompt": "开始"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    sub = r.json()
    subconv_id = sub["subagent_conversation_id"]
    assert sub["status"] == "starting"
    assert sub["unread_count"] == 0

    listed = await client.get("/api/v1/conversations", headers=auth_headers)
    listed_ids = {c["id"] for c in listed.json()}
    assert parent_id in listed_ids
    assert subconv_id not in listed_ids

    detail = await client.get(f"/api/v1/conversations/{parent_id}/subagents", headers=auth_headers)
    assert detail.status_code == 200
    assert [s["id"] for s in detail.json()] == [sub["id"]]
