"""Clarify protocol v2 (LIST + BLPOP) handshake tests.

Covers the race that broke the legacy GET/pubsub design: the answer being
delivered *before* the agent starts waiting must still unblock it. Also
covers event-stream replay and cancel interrupting a clarify wait.

Requires reachable Redis (skipped otherwise).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import pytest_asyncio

from app.core import redis as R


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis():
    """The cached redis client binds to the creating event loop; each test
    runs in a new loop (asyncio_mode=auto), so recreate it per test."""
    await R.close_redis()
    yield
    await R.close_redis()


async def _redis_ok() -> bool:
    try:
        await R.get_redis().ping()
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_blpop_unblocks_even_when_response_arrives_first():
    """The core v2 property: RPUSH before BLPOP still delivers (race-free)."""
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    sid, clarify_id = uuid.uuid4().hex, uuid.uuid4().hex[:12]

    from agent_runner.runner_clarify import deliver_clarify_response
    assert await deliver_clarify_response(sid, clarify_id, "选项A")

    # Agent-side wait happens AFTER the answer was pushed — must return at once.
    res = await R.get_redis().blpop(R.clarify_resp_key(sid, clarify_id), timeout=2)
    assert res is not None
    assert res[1] == "选项A"


@pytest.mark.asyncio
async def test_pop_clarify_request_consumes_v2_then_legacy():
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    sid = uuid.uuid4().hex
    r = R.get_redis()

    # v2 list entry
    await r.rpush(R.clarify_req_key(sid), json.dumps({"clarify_id": "c1", "question": "Q1"}))
    # legacy pending key (dual-protocol compat)
    await r.set(f"hermes:clarify_pending:{sid}", json.dumps({"clarify_id": "c2", "question": "Q2"}))

    from agent_runner.runner_clarify import pop_clarify_request
    first = await pop_clarify_request(sid)
    second = await pop_clarify_request(sid)
    third = await pop_clarify_request(sid)

    assert first and first["clarify_id"] == "c1"
    assert second and second["clarify_id"] == "c2"
    assert third is None  # both consumed exactly once


@pytest.mark.asyncio
async def test_queued_clarify_requests_do_not_overwrite():
    """Two concurrent clarifies queue up instead of clobbering one key."""
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    sid = uuid.uuid4().hex
    r = R.get_redis()
    await r.rpush(R.clarify_req_key(sid), json.dumps({"clarify_id": "a", "question": "QA"}))
    await r.rpush(R.clarify_req_key(sid), json.dumps({"clarify_id": "b", "question": "QB"}))

    from agent_runner.runner_clarify import pop_clarify_request
    got = [await pop_clarify_request(sid), await pop_clarify_request(sid)]
    assert [g["clarify_id"] for g in got] == ["a", "b"]


@pytest.mark.asyncio
async def test_event_stream_replays_after_publish():
    """Events published before anyone reads are NOT lost (unlike Pub/Sub)."""
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    cid = str(uuid.uuid4())
    await R.publish_event(cid, {"type": "start", "message_id": "m1"})
    await R.publish_event(cid, {"type": "token", "message_id": "m1", "delta": "你好"})

    entries = await R.read_events(cid, "0-0", block_ms=100)
    events = [json.loads(d) for _id, d in entries]
    assert [e["type"] for e in events] == ["start", "token"]
    # conversation_id is injected centrally so clients can scope events
    assert all(e["conversation_id"] == cid for e in events)


@pytest.mark.asyncio
async def test_wait_for_confirmation_interrupted_by_cancel():
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    cid = str(uuid.uuid4())
    await R.request_cancel(cid)
    try:
        resp = await asyncio.wait_for(
            R.wait_for_confirmation(cid, "req-1", timeout=30, cancel_check=True),
            timeout=5,
        )
    finally:
        await R.clear_cancel(cid)
    assert resp == {"choice": "已取消"}


@pytest.mark.asyncio
async def test_interactive_clarify_round_trip(monkeypatch):
    """Modal path end-to-end (without ACP): request → confirmation_request
    event → user confirm → agent-side BLPOP unblocked with the choice."""
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    from app.config import settings
    monkeypatch.setattr(settings, "clarify_strategy", "interactive")

    cid = str(uuid.uuid4())
    sid = uuid.uuid4().hex
    message_id = str(uuid.uuid4())
    acc: dict = {}

    from agent_runner.runner_clarify import handle_clarify_request
    await handle_clarify_request(
        cid, message_id, acc, sid,
        {"clarify_id": "cl-1", "question": "用哪个方案？", "options": ["方案A", "方案B"]},
        bg_tasks=set(),
    )

    # The confirmation_request event reached the conversation stream
    entries = await R.read_events(cid, "0-0", block_ms=500)
    events = [json.loads(d) for _id, d in entries]
    reqs = [e for e in events if e["type"] == "confirmation_request"]
    assert reqs and reqs[0]["request"]["id"] == "cl-1"
    assert acc["clarifies"][0]["status"] == "pending"

    # User answers via the API path
    await R.respond_to_confirmation(cid, "cl-1", "方案B")

    # Agent side unblocks with the chosen answer
    res = await asyncio.wait_for(
        R.get_redis().blpop(R.clarify_resp_key(sid, "cl-1"), timeout=10), timeout=12
    )
    assert res[1] == "方案B"

    # Wait for the background task to record the outcome
    for _ in range(50):
        if acc["clarifies"][0].get("status") == "answered":
            break
        await asyncio.sleep(0.1)
    assert acc["clarifies"][0]["status"] == "answered"
    assert acc["clarifies"][0]["choice"] == "方案B"
