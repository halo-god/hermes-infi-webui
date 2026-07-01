"""Group chat / roundtable must not let an agent block on an interactive
clarify request — nobody can answer a confirmation modal mid-roundtable.

Exercises the actual handle_roundtable() poll-and-auto-decline path with a
mocked ACPClient (no real agent subprocess needed): the fake agent "calls"
clarify by pushing a request onto its session's clarify queue mid-turn, and
the test asserts the runner drains and auto-declines it instead of hanging.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.core import redis as R
from agent_runner import runner_roundtable as rt


async def _redis_ok() -> bool:
    try:
        await R.get_redis().ping()
        return True
    except Exception:
        return False


class _FakeAgent:
    command = ["fake"]
    label = "Fake"
    color = "#000"
    description = ""


class _FakeACPClient:
    """Simulates an agent that calls the clarify tool mid-turn, then finishes."""

    def __init__(self, *_args, on_update=None, **_kwargs):
        self.session_id = f"fake-session-{uuid.uuid4().hex[:8]}"
        self.on_update = on_update

    async def start(self):
        pass

    async def initialize(self):
        return {}

    async def new_session(self, _cwd, mcp_servers=None):
        return self.session_id

    async def prompt(self, _content):
        await R.get_redis().rpush(
            R.clarify_req_key(self.session_id),
            json.dumps({"clarify_id": "cl-1", "question": "选哪个方案？", "options": ["A", "B"]}),
        )
        # Give the poll loop a couple of ticks to notice and auto-decline
        # before the (fake) agent "finishes" its turn.
        await asyncio.sleep(1.5)
        if self.on_update:
            await self.on_update({
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "done answering"},
            })
        return "end_turn"

    async def stop(self):
        pass


@pytest.mark.asyncio
async def test_roundtable_auto_declines_clarify_instead_of_hanging(monkeypatch):
    if not await _redis_ok():
        pytest.skip("Redis not reachable")

    conversation_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())

    monkeypatch.setattr(rt, "ACPClient", _FakeACPClient)

    async def _noop_finalize(*_args, **_kwargs):
        return None

    monkeypatch.setattr(rt, "_finalize_roundtable", _noop_finalize)

    task = {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "text": "讨论一下这个方案",
        "targets": [
            {"agent_id": "claude", "profile_id": None, "system_prompt": None, "profile_dir": None},
        ],
    }

    # Must complete promptly — if clarify weren't auto-declined, the fake
    # agent's own logic doesn't hang, but a real agent's clarify tool call
    # would block on BLPOP until clarify_timeout_seconds (240s default).
    await asyncio.wait_for(rt.handle_roundtable(task, {"claude": _FakeAgent()}), timeout=10)

    # The clarify request was drained and answered (auto-declined with an
    # empty choice) rather than left sitting unconsumed in the queue.
    session_key_prefix = "fake-session-"
    keys = await R.get_redis().keys(f"hermes:clarify:req:{session_key_prefix}*")
    assert keys == []  # request list fully drained

    events = await R.read_events(conversation_id, "0-0", block_ms=200)
    types = [json.loads(d)["type"] for _id, d in events]
    assert "rt_start" in types
    assert "done" in types
    # No confirmation_request/modal was ever surfaced to the conversation.
    assert "confirmation_request" not in types
