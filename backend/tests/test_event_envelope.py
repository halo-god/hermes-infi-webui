"""Normalized event envelope: ts timestamp + volatile flag injection.

The choke point is publish_event / publish_user_event (app/core/redis.py) —
every one of the ~45 producer call sites gets the envelope for free.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.core import redis as R
from app.core.redis import VOLATILE_EVENT_TYPES


@pytest.mark.asyncio
async def test_publish_event_injects_ts_on_all_events():
    cid = f"env-{uuid.uuid4().hex[:12]}-a1"
    await R.publish_event(cid, {"type": "done", "message_id": "m1", "status": "complete"})
    entries = await R.read_events(cid, "0-0", block_ms=1)
    assert entries, "event should be readable back"
    ev = json.loads(entries[-1][1])
    assert "ts" in ev and ev["ts"], "every event carries an ISO timestamp"
    assert "v" not in ev, "done is not volatile — must not carry the flag"


@pytest.mark.asyncio
async def test_publish_event_marks_volatile_deltas():
    cid = f"env-{uuid.uuid4().hex[:12]}-a2"
    await R.publish_event(cid, {"type": "token", "message_id": "m1", "delta": "x"})
    await R.publish_event(cid, {"type": "thought", "message_id": "m1", "delta": "y"})
    await R.publish_event(cid, {"type": "tool_call", "message_id": "m1", "title": "t"})
    entries = await R.read_events(cid, "0-0", block_ms=1)
    types = [json.loads(d)["type"] for _, d in entries]
    assert types == ["token", "thought", "tool_call"]
    token_ev, thought_ev, tool_ev = (json.loads(d) for _, d in entries)
    assert token_ev.get("v") is True
    assert thought_ev.get("v") is True
    assert "v" not in tool_ev, "tool_call is durable — no volatile flag"


def test_volatile_set_covers_deltas_only():
    for t in ("token", "thought", "rt_token", "merge_token", "chain_step_token", "typing"):
        assert t in VOLATILE_EVENT_TYPES
    # Critical, must-deliver events must never be marked volatile.
    for t in ("done", "error", "confirmation_request", "file", "message",
              "tool_blocked", "session_info"):
        assert t not in VOLATILE_EVENT_TYPES


@pytest.mark.asyncio
async def test_publish_user_event_gets_the_same_envelope():
    uid = f"env-{uuid.uuid4().hex[:12]}-b1"
    await R.publish_user_event(uid, {"type": "notify", "title": "t"})
    entries = await R.read_user_events(uid, "0-0", block_ms=1)
    assert entries
    ev = json.loads(entries[-1][1])
    assert ev["ts"]
    assert ev["user_id"] == uid


@pytest.mark.asyncio
async def test_existing_ts_is_not_overwritten():
    cid = f"env-{uuid.uuid4().hex[:12]}-a3"
    await R.publish_event(cid, {"type": "token", "message_id": "m", "delta": "d",
                                "ts": "2026-01-01T00:00:00+00:00"})
    entries = await R.read_events(cid, "0-0", block_ms=1)
    ev = json.loads(entries[-1][1])
    assert ev["ts"] == "2026-01-01T00:00:00+00:00", "setdefault semantics — producer wins"


@pytest.mark.asyncio
async def test_summary_worker_publishes_summary_generated(db, monkeypatch):
    """The compaction is auditable on the live stream, not just in the DB."""
    import uuid as _uuid
    from unittest.mock import AsyncMock

    from app.config import settings
    from app.db.base import async_session_maker
    from app.db.models.conversation import Conversation
    from app.db.models.user import User
    from app.core.security import hash_password
    from agent_runner import runner_conversation_summary as rcs
    from app.services import summarizer

    monkeypatch.setattr(settings, "summary_enabled", True)
    monkeypatch.setattr(settings, "summary_increment_threshold", 1)
    # NOTE preserve_recent=0 is a trap: all_msgs[:-0] == [] — keep 1.
    monkeypatch.setattr(settings, "summary_preserve_recent", 1)

    async with async_session_maker() as s:
        user = User(
            id=_uuid.uuid4(), email=f"env-{_uuid.uuid4().hex[:8]}@h.io", name="e",
            password_hash=hash_password("Test@1234"), is_active=True, role="member",
        )
        s.add(user)
        await s.commit()
    async with async_session_maker() as s:
        convo = Conversation(
            id=_uuid.uuid4(), owner_id=user.id, title="t", type="personal",
            primary_agent_id="hermes",
        )
        s.add(convo)
        await s.commit()
        cid = str(convo.id)

    from app.db.models.conversation import Message

    async with async_session_maker() as s:
        s.add(Message(
            id=_uuid.uuid4(), conversation_id=convo.id, owner_id=user.id,
            role="user", content={"text": "讨论方案A"}, status="complete",
        ))
        s.add(Message(
            id=_uuid.uuid4(), conversation_id=convo.id, owner_id=user.id,
            role="agent", agent_id="hermes", content={"text": "方案A可行"}, status="complete",
        ))
        await s.commit()

    published: list[dict] = []
    monkeypatch.setattr(
        rcs.R, "publish_event",
        AsyncMock(side_effect=lambda _cid, ev: published.append(ev)),
    )

    class _FakeResult:
        summary = "【决策】采用方案A"
        token_estimate = 30

    monkeypatch.setattr(
        summarizer, "summarize_sync", lambda text: _FakeResult()
    )

    await rcs.handle_conversation_summary({"conversation_id": cid})

    assert published, "worker must publish a summary_generated event"
    ev = published[0]
    assert ev["type"] == "summary_generated"
    assert ev["conversation_id"] == cid
    assert ev["covered_count"] == 1
    assert "方案A" in ev["preview"]
