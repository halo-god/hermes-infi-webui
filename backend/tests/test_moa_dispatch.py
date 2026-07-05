"""MoA ("mixture of agents") profiles must fan out via the existing roundtable
executor — no new merge logic, just a different way of building `targets`.

Selecting an is_moa Profile in an otherwise single-agent conversation must
route dispatch() to send_roundtable() (not send_message()), with `targets`
resolved from moa_target_profile_ids and the enqueued task flagged moa=True
so the frontend can render it as a synthesized answer.
"""
from __future__ import annotations

import json
import uuid

from app.core import redis as redis_core
from app.core.security import hash_password
from app.db.models.agent import Profile
from app.db.models.user import User
from app.services import conversation_service as svc


async def _mk_user(db, email: str) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_profile(db, **kwargs) -> Profile:
    defaults = dict(
        id=uuid.uuid4(), name="助手", handle=f"p-{uuid.uuid4().hex[:8]}",
        default_agent_id="hermes", is_active=True,
    )
    defaults.update(kwargs)
    p = Profile(**defaults)
    db.add(p)
    await db.flush()
    return p


async def _latest_enqueued_task(redis) -> dict:
    entries = await redis.xrevrange("acp:prompt", count=1)
    _id, fields = entries[0]
    return json.loads(fields["data"])


async def test_moa_profile_routes_to_roundtable(db):
    owner = await _mk_user(db, "moa-owner@h.io")
    t1 = await _mk_profile(db, name="claude风格", default_agent_id="claude", system_prompt="你是claude人设")
    t2 = await _mk_profile(db, name="critic风格", default_agent_id="critic", system_prompt="你是critic人设")
    moa = await _mk_profile(db, name="综合助手", is_moa=True, moa_target_profile_ids=[str(t1.id), str(t2.id)])

    convo = await svc.create_conversation(
        db, owner.id, title=None, primary_agent_id="hermes", profile_id=None,
    )
    assert convo.active_agent_ids in (None, ["hermes"])  # single-agent conversation

    user_msg, agent_msg = await svc.dispatch(
        db, convo, "怎么看这个方案？", owner_id=owner.id, profile_id_override=str(moa.id),
    )

    assert agent_msg.role == "roundtable"
    assert agent_msg.content["moa"] is True
    reply_agent_ids = {r["agent_id"] for r in agent_msg.content["replies"]}
    assert reply_agent_ids == {"claude", "critic"}

    task = await _latest_enqueued_task(redis_core.get_redis())
    assert task["type"] == "roundtable"
    assert task["moa"] is True
    target_agent_ids = {t["agent_id"] for t in task["targets"]}
    assert target_agent_ids == {"claude", "critic"}
    claude_target = next(t for t in task["targets"] if t["agent_id"] == "claude")
    assert "你是claude人设" in (claude_target["system_prompt"] or "")


async def test_moa_profile_without_targets_falls_back_to_single(db):
    owner = await _mk_user(db, "moa-empty@h.io")
    moa = await _mk_profile(db, name="空综合助手", is_moa=True, moa_target_profile_ids=[])
    convo = await svc.create_conversation(
        db, owner.id, title=None, primary_agent_id="hermes", profile_id=None,
    )

    _, agent_msg = await svc.dispatch(
        db, convo, "hello", owner_id=owner.id, profile_id_override=str(moa.id),
    )

    assert agent_msg.role == "agent"  # single-agent path, not roundtable

    task = await _latest_enqueued_task(redis_core.get_redis())
    assert task["type"] == "single"


async def test_non_moa_profile_unaffected(db):
    owner = await _mk_user(db, "plain-owner@h.io")
    plain = await _mk_profile(db, name="普通助手", is_moa=False)
    convo = await svc.create_conversation(
        db, owner.id, title=None, primary_agent_id="hermes", profile_id=None,
    )

    _, agent_msg = await svc.dispatch(
        db, convo, "hi", owner_id=owner.id, profile_id_override=str(plain.id),
    )

    assert agent_msg.role == "agent"
    task = await _latest_enqueued_task(redis_core.get_redis())
    assert task["type"] == "single"
