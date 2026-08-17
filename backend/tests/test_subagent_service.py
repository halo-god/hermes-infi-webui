"""subagent_service unit tests — spawn/list/get lifecycle (the runner-side
execution is covered by test_subagent_pool).
"""
from __future__ import annotations

import uuid

import pytest

from app.db.models.conversation import Conversation
from app.services import subagent_service as svc
from app.core.security import hash_password
from app.db.models.user import User

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email: str) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_parent(db, owner: User) -> Conversation:
    c = Conversation(owner_id=owner.id, title="父会话", primary_agent_id="hermes")
    db.add(c)
    await db.flush()
    return c


async def test_spawn_creates_subagent_and_headless_convo(db):
    from sqlalchemy import select
    owner = await _mk_user(db, "sa-spawn@h.io")
    parent = await _mk_parent(db, owner)

    sub = await svc.spawn_subagent(
        db, parent, owner.id, purpose="调研任务", initial_prompt="查一下",
        agent_id="hermes",
    )
    assert sub.parent_conversation_id == parent.id
    assert sub.owner_id == owner.id
    assert sub.status in ("pending", "spawning", "starting", "running")

    # A headless subagent conversation was created
    conv = (await db.execute(
        select(Conversation).where(Conversation.id == sub.subagent_conversation_id)
    )).scalar_one_or_none()
    assert conv is not None
    assert conv.type == "subagent"
    assert conv.owner_id == owner.id


async def test_spawn_with_profile_id(db):
    from app.db.models.agent import Profile
    owner = await _mk_user(db, "sa-prof@h.io")
    parent = await _mk_parent(db, owner)
    profile = Profile(
        id=uuid.uuid4(), name="调研助手", handle="research", default_agent_id="hermes",
        scope="personal", system_prompt="你是调研员",
    )
    db.add(profile)
    await db.flush()

    sub = await svc.spawn_subagent(
        db, parent, owner.id, purpose="带人设任务", initial_prompt="x",
        profile_id=profile.id,
    )
    assert sub.profile_id == profile.id


async def test_list_and_get_subagents(db):
    owner = await _mk_user(db, "sa-list@h.io")
    parent = await _mk_parent(db, owner)
    await svc.spawn_subagent(db, parent, owner.id, purpose="任务A", initial_prompt="x")

    rows = await svc.list_subagents(db, parent.id)
    assert any(r.purpose == "任务A" for r, _ in rows), f"got {[r.purpose for r, _ in rows]}"

    row = await svc.get_subagent(db, parent.id, rows[0][0].id)
    assert row is not None
    assert await svc.get_subagent(db, parent.id, uuid.uuid4()) is None


async def test_mark_read_and_stop_are_idempotent(db):
    owner = await _mk_user(db, "sa-ops@h.io")
    parent = await _mk_parent(db, owner)
    sub = await svc.spawn_subagent(db, parent, owner.id, purpose="操作任务", initial_prompt="x")

    await svc.mark_subagent_read(db, sub)
    # request_stop must not raise even though no runner is consuming
    await svc.request_stop_subagent(sub)
