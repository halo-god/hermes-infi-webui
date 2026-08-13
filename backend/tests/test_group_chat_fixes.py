"""Group-chat fixes: per-AI thinking in session logs, clarify auto-decline
for group single-agent turns, and personal-chat follow-up (mentions) routing.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest import mock

import pytest

from app.core import redis as R
from app.db.models.conversation import Conversation, Message
from app.db.models.session_log import SessionCallLog
from agent_runner import runner as runner_mod


async def _redis_ok() -> bool:
    try:
        await R.get_redis().ping()
        return True
    except Exception:
        return False


# ── clarify auto-decline: group single-agent turn ──────────────────────────


class _FakeAgent:
    command = ["fake"]
    label = "Fake"
    color = "#000"
    description = ""


class _FakePoolClient:
    """Mimics session_pool.get()'s return: (client, session_id)."""

    def __init__(self, *args, **kwargs):
        self.session_id = f"fake-session-{uuid.uuid4().hex[:8]}"
        # handle_single passes on_update/on_fs_write as positional args 4/5.
        self.on_update = kwargs.get("on_update") or (args[3] if len(args) > 3 else None)
        self._proc = None  # runner logs client._proc.pid at spawn

    async def start(self):
        pass

    async def initialize(self):
        return {}

    async def new_session(self, _cwd, mcp_servers=None):
        return self.session_id

    async def set_session_mode(self, _sid, _mode):
        pass

    async def prompt(self, _content):
        # The fake agent "calls" clarify mid-turn, then answers normally.
        await R.get_redis().rpush(
            R.clarify_req_key(self.session_id),
            json.dumps({"clarify_id": "cl-1", "question": "选哪个？", "options": ["A", "B"]}),
        )
        await asyncio.sleep(1.2)
        if self.on_update:
            await self.on_update({
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "done"},
            })
        return "end_turn"

    async def cancel(self):
        pass

    async def stop(self):
        pass


class _FakePool:
    def __init__(self, client=None):
        self._client = client

    async def get(self, *args, **kwargs):
        # handle_single passes on_update/on_fs_write positionally after the
        # first args; the client must see them or the fake agent never talks.
        if self._client is None:
            self._client = _FakePoolClient(*args, **kwargs)
        return self._client, self._client.session_id

    async def drop(self, *args, **kwargs):
        pass


class _FakeWatcher:
    """WorkspaceWatcher stand-in: scan_existing is awaited by the runner."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    async def scan_existing(self):
        pass


@pytest.mark.asyncio
async def test_group_single_turn_auto_declines_clarify(db, test_user, monkeypatch):
    """A group chat's single-agent (@1 AI) turn must NOT surface a clarify
    modal — the runner drains the request and answers it with an empty choice."""
    if not await _redis_ok():
        pytest.skip("Redis not reachable")

    # Create via async_session_maker (the runner reads through it; the `db`
    # fixture's wrapped transaction is invisible to other connections).
    from app.core.security import hash_password
    from app.db.base import async_session_maker
    from app.db.models.user import User
    user = User(
        id=uuid.uuid4(), email="gcf@h.io", name="gcf",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    convo = Conversation(
        id=uuid.uuid4(), owner_id=user.id, type="group",
        title="群聊测试", primary_agent_id="hermes",
    )
    async with async_session_maker() as s:
        s.add(user)
        await s.flush()
        s.add(convo)
        await s.commit()

    pool = _FakePool()
    runner = runner_mod.Runner.__new__(runner_mod.Runner)
    runner.agents = {"hermes": _FakeAgent()}
    runner.pool = pool
    runner._bg_tasks = set()
    runner._watchers = {}
    runner._load_high_risk_server_names = mock.AsyncMock(return_value=[])
    runner._set_session_id = mock.AsyncMock()
    runner._finalize = mock.AsyncMock()
    runner._fail = mock.AsyncMock()
    runner._extract_and_save_files = mock.AsyncMock()
    runner._record_skill_firings = mock.AsyncMock()
    runner._record_profile_firing = mock.AsyncMock()
    monkeypatch.setattr(runner_mod, "WorkspaceWatcher", _FakeWatcher)

    task = {
        "conversation_id": str(convo.id),
        "message_id": str(uuid.uuid4()),
        "agent_id": "hermes",
        "text": "帮我确认一下",
    }
    await asyncio.wait_for(runner.handle_single(task), timeout=10)
    fake = pool._client  # created during get() with the real on_update

    # The clarify request was drained & answered — nothing left in the queue.
    keys = await R.get_redis().keys(f"hermes:clarify:req:{fake.session_id}*")
    assert keys == [], "group single-agent turn must drain the clarify queue"

    # No confirmation modal event was ever published to the conversation.
    events = await R.read_events(str(convo.id), "0-0", block_ms=200)
    types = [json.loads(d)["type"] for _id, d in events]
    assert "confirmation_request" not in types, "group turns must never show a modal"
    assert runner._finalize.await_count >= 1


# ── personal-chat follow-up: mentions single-target routing ────────────────


@pytest.mark.asyncio
async def test_personal_dispatch_mentions_resolves_profile(db, test_user):
    """_resolve_personal_mentions maps profile:{id} to that profile's agent."""
    from app.db.models.agent import Profile
    from app.services import conversation_service as svc

    p = Profile(
        id=uuid.uuid4(), name="情感大师", handle="emotion-master",
        default_agent_id="hermes", scope="personal", is_active=True,
    )
    db.add(p)
    await db.commit()

    agent_id, profile_id = await svc._resolve_personal_mentions(
        db, [f"profile:{p.id}"],
    )
    assert agent_id == "hermes"
    assert profile_id == str(p.id)

    # Multiple distinct targets → (None, None) → caller falls back to roundtable.
    p2 = Profile(
        id=uuid.uuid4(), name="另一个", handle="other",
        default_agent_id="claude", scope="personal", is_active=True,
    )
    db.add(p2)
    await db.commit()
    agent_id, profile_id = await svc._resolve_personal_mentions(
        db, [f"profile:{p.id}", f"profile:{p2.id}"],
    )
    assert agent_id is None and profile_id is None


@pytest.mark.asyncio
async def test_personal_dispatch_mentions_targets_single_agent(db, test_user, monkeypatch):
    """dispatch(mentions=[profile:...]) must route to that profile alone via
    agent_id_override (single-agent send, not the roundtable branch)."""
    from app.db.models.agent import Profile
    from app.services import conversation_service as svc

    p = Profile(
        id=uuid.uuid4(), name="情感大师", handle="emotion-master",
        default_agent_id="hermes", scope="personal", is_active=True,
        system_prompt="你是情感大师",
    )
    db.add(p)
    await db.commit()

    convo = Conversation(
        id=uuid.uuid4(), owner_id=test_user.id, type="personal",
        title="追问测试", primary_agent_id="hermes",
        active_agent_ids=["hermes", "claude"],  # would be roundtable w/o mention
    )
    db.add(convo)
    await db.commit()

    sent = {}

    async def _fake_send_message(db_, convo_, text_, **kwargs):
        sent.update(kwargs)
        return (
            Message(id=uuid.uuid4(), conversation_id=convo_.id, role="user", content={"text": text_}),
            Message(id=uuid.uuid4(), conversation_id=convo_.id, role="agent", content={"text": "ok"}),
        )

    monkeypatch.setattr(svc, "send_message", _fake_send_message)

    await svc.dispatch(
        db, convo, "继续讲讲", owner_id=test_user.id,
        mentions=[f"profile:{p.id}"],
    )
    assert sent.get("agent_id_override") == "hermes", \
        "mentions must force single-agent routing to the mentioned profile"
    assert sent.get("profile_id") == str(p.id)


# ── session log detail: roundtable replies with per-AI thinking & calls ────


@pytest.mark.asyncio
async def test_session_detail_roundtable_replies_with_thinking(db, test_user):
    """session_detail exposes per-AI replies (text + thinking) and attributes
    session_call_logs to each AI via agent_id."""
    from app.services import session_log_service

    convo = Conversation(
        id=uuid.uuid4(), owner_id=test_user.id, type="group",
        title="日志测试", primary_agent_id="hermes",
    )
    db.add(convo)
    await db.flush()

    user_msg = Message(
        id=uuid.uuid4(), conversation_id=convo.id, role="user",
        owner_id=test_user.id, content={"text": "讨论方案"}, status="complete",
    )
    rt_msg = Message(
        id=uuid.uuid4(), conversation_id=convo.id, role="roundtable",
        content={
            "replies": [
                {
                    "agent_id": "hermes", "profile_id": None,
                    "text": "方案A", "status": "complete",
                    "thinking": "我思考了方案A",
                },
                {
                    "agent_id": "claude", "profile_id": None,
                    "text": "方案B", "status": "complete",
                    "thinking": "我思考了方案B",
                },
            ],
            "merged": {"text": "综合结论", "status": "complete"},
        },
        status="complete",
    )
    db.add_all([user_msg, rt_msg])
    await db.flush()

    db.add(SessionCallLog(
        conversation_id=convo.id, message_id=rt_msg.id, agent_id="hermes",
        kind="model", name="hermes", status="completed",
        tokens_in=10, tokens_out=5,
        started_at=datetime.now(timezone.utc), ended_at=datetime.now(timezone.utc),
    ))
    db.add(SessionCallLog(
        conversation_id=convo.id, message_id=rt_msg.id, agent_id="claude",
        kind="tool", name="read_file", tool_kind="read", status="completed",
        started_at=datetime.now(timezone.utc), ended_at=datetime.now(timezone.utc),
    ))
    await db.commit()

    detail = await session_log_service.session_detail(db, convo.id)
    assert detail is not None
    # Group header summarizes the GROUP (no team → falls back to the title),
    # not the chat owner.
    assert detail["user_name"] == "日志测试"
    turn = detail["turns"][0]
    assert turn["agent_text"] == "综合结论"  # merged stays the summary
    # The turn's user input carries the actual sender's name.
    assert turn["user_name"] == test_user.name
    assert turn["replies"] is not None and len(turn["replies"]) == 2

    r0 = turn["replies"][0]
    assert r0["agent_id"] == "hermes"
    assert r0["thinking"] == "我思考了方案A"
    assert r0["text"] == "方案A"
    # Per-AI call attribution: hermes has its model call, claude its tool call.
    assert len(r0["calls"]) == 1 and r0["calls"][0]["kind"] == "model"
    r1 = turn["replies"][1]
    assert r1["thinking"] == "我思考了方案B"
    assert len(r1["calls"]) == 1 and r1["calls"][0]["kind"] == "tool"
