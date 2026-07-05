"""Skill-firing instrumentation: which skill(s) fired for which message.

_build_skills_prompt()'s matched-id threading is covered indirectly by
test_layered_memory.py; these tests exercise the runner-side write path
(handle_single -> _record_skill_firings), which needs a genuinely-committing
session since the runner opens its own async_session_maker() connection —
see test_subagent_pool.py's `real_db` fixture for the same cross-connection
visibility issue and why the shared `db` fixture can't be used here.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from agent_runner import session_pool as sp
from agent_runner.runner import Runner
from app.core.security import hash_password
from app.db.base import async_session_maker
from app.db.models.conversation import Conversation, Message
from app.db.models.skill_evolution import SkillFiring
from app.db.models.user import User


class FakeProc:
    returncode = None
    pid = 4242


class FakeACPClient:
    default_reply = "已经处理好了"

    def __init__(self, command, cwd, *, protocol_version=1, on_update=None, on_fs_write=None, env=None):
        self.on_update = on_update
        self.on_fs_write = on_fs_write
        self._closed = False
        self._proc = FakeProc()
        self._session_id = None
        self.reply_text = type(self).default_reply

    async def start(self):
        pass

    async def initialize(self):
        return {}

    async def new_session(self, cwd, mcp_servers=None):
        self._session_id = f"sess-{uuid.uuid4().hex[:8]}"
        return self._session_id

    async def resume_session(self, session_id, cwd, mcp_servers=None):
        raise RuntimeError("not used in this test")

    async def set_session_mode(self, session_id, mode_id):
        pass

    async def prompt(self, content):
        if self.on_update:
            await self.on_update({"sessionUpdate": "agent_message_chunk", "content": {"text": self.reply_text}})
        return "end_turn"

    async def stop(self):
        self._closed = True


@pytest_asyncio.fixture
async def real_db():
    """See test_subagent_pool.py's identical fixture for why this can't be
    the shared `db` fixture: handle_single opens its own async_session_maker()
    session, which can't see data staged through the rollback-only `db`
    fixture's SAVEPOINT-joined transaction."""
    session = async_session_maker()
    owner_ids: list[uuid.UUID] = []
    try:
        yield session, owner_ids
    finally:
        for uid in owner_ids:
            await session.execute(delete(User).where(User.id == uid))
        await session.commit()
        await session.close()


@pytest.fixture
def runner_with_fake_agent(monkeypatch):
    monkeypatch.setattr(sp, "ACPClient", FakeACPClient)
    r = Runner()
    r.agents = {"hermes": SimpleNamespace(command=["hermes", "acp"])}
    return r


async def _mk_user(db, owner_ids, email: str) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    owner_ids.append(u.id)
    return u


async def _mk_skill(db, owner: User, **kwargs):
    from app.services import memory_service
    defaults = dict(
        name="限流技能", description="限流相关问题",
        content="回答限流问题时提醒检查 rl:msg:{user} 键",
        trigger_conditions={"keywords": ["限流"]}, owner_id=owner.id,
    )
    defaults.update(kwargs)
    return await memory_service.create_skill(db, **defaults)


async def _mk_conversation(db, owner: User) -> Conversation:
    convo = Conversation(owner_id=owner.id, title="新会话", primary_agent_id="hermes")
    db.add(convo)
    await db.commit()
    await db.refresh(convo)
    return convo


async def _mk_agent_message(db, convo: Conversation) -> Message:
    msg = Message(conversation_id=convo.id, role="agent", agent_id="hermes", content={"text": ""}, status="streaming")
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def test_completed_turn_records_skill_firings(real_db, runner_with_fake_agent):
    db, owner_ids = real_db
    owner = await _mk_user(db, owner_ids, "firing-owner@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    agent_msg = await _mk_agent_message(db, convo)

    task = {
        "conversation_id": str(convo.id),
        "message_id": str(agent_msg.id),
        "agent_id": "hermes",
        "text": "Redis 限流怎么做",
        "matched_skill_ids": [str(skill.id)],
        "skill_firing_excerpt": "Redis 限流怎么做",
    }
    await runner_with_fake_agent.handle_single(task)

    rows = list((await db.execute(
        select(SkillFiring).where(SkillFiring.skill_id == skill.id)
    )).scalars().all())
    assert len(rows) == 1
    assert rows[0].message_id == agent_msg.id
    assert rows[0].conversation_id == convo.id
    assert rows[0].owner_id == owner.id
    assert rows[0].trigger_query_excerpt == "Redis 限流怎么做"


async def test_no_matched_skills_records_nothing(real_db, runner_with_fake_agent):
    db, owner_ids = real_db
    owner = await _mk_user(db, owner_ids, "no-firing-owner@h.io")
    convo = await _mk_conversation(db, owner)
    agent_msg = await _mk_agent_message(db, convo)

    task = {
        "conversation_id": str(convo.id),
        "message_id": str(agent_msg.id),
        "agent_id": "hermes",
        "text": "随便聊聊",
    }
    await runner_with_fake_agent.handle_single(task)

    rows = list((await db.execute(
        select(SkillFiring).where(SkillFiring.conversation_id == convo.id)
    )).scalars().all())
    assert rows == []


async def test_failed_turn_does_not_record_firing(real_db, runner_with_fake_agent, monkeypatch):
    db, owner_ids = real_db
    owner = await _mk_user(db, owner_ids, "fail-owner@h.io")
    skill = await _mk_skill(db, owner, name="另一个技能")
    convo = await _mk_conversation(db, owner)
    agent_msg = await _mk_agent_message(db, convo)

    # Force an empty agent reply -> handle_single treats it as a failure
    # (context-overflow-style refusal) and must not record a firing.
    monkeypatch.setattr(FakeACPClient, "default_reply", "")

    task = {
        "conversation_id": str(convo.id),
        "message_id": str(agent_msg.id),
        "agent_id": "hermes",
        "text": "限流问题",
        "matched_skill_ids": [str(skill.id)],
    }
    await runner_with_fake_agent.handle_single(task)

    rows = list((await db.execute(
        select(SkillFiring).where(SkillFiring.skill_id == skill.id)
    )).scalars().all())
    assert rows == []
