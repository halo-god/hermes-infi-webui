"""Layered memory: pg_trgm episodic retrieval + trigger-matched skills.

Covers the service-layer search functions directly (search_episodes,
search_skills) and their wiring into conversation_service's system-prompt
builders — the retrieval-quality claim this design rests on (trigram over
tsvector for CJK text) is only meaningful if it's actually exercised in
Chinese, so these tests use Chinese sample text throughout.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.security import hash_password
from app.db.models.agent import Profile
from app.db.models.user import User
from app.services import conversation_service as svc
from app.services import memory_service


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


# ── search_episodes ──

async def test_search_episodes_matches_relevant_chinese_text(db):
    owner = await _mk_user(db, "ep-owner@h.io")
    now = datetime.now(tz=timezone.utc)
    await memory_service.add_episode(
        db, owner.id, None, "重构讨论", "用户和AI讨论了如何用 pg_trgm 重构记忆检索模块", 500, now,
    )
    await memory_service.add_episode(
        db, owner.id, None, "旅行计划", "用户询问下个月去日本旅行的行程安排", 300, now,
    )

    results = await memory_service.search_episodes(db, owner.id, "记忆检索要怎么做")

    assert results
    assert results[0].title == "重构讨论"


async def test_search_episodes_scoped_to_owner(db):
    owner = await _mk_user(db, "ep-owner2@h.io")
    other = await _mk_user(db, "ep-other@h.io")
    now = datetime.now(tz=timezone.utc)
    await memory_service.add_episode(db, other.id, None, "别人的会话", "别人聊的完全不相关的内容", 100, now)

    results = await memory_service.search_episodes(db, owner.id, "别人的会话")
    assert results == []


async def test_search_episodes_empty_query_returns_nothing(db):
    owner = await _mk_user(db, "ep-owner3@h.io")
    now = datetime.now(tz=timezone.utc)
    await memory_service.add_episode(db, owner.id, None, "标题", "摘要内容", 10, now)
    assert await memory_service.search_episodes(db, owner.id, "   ") == []


# ── search_skills ──

async def test_search_skills_keyword_trigger(db):
    owner = await _mk_user(db, "sk-owner@h.io")
    await memory_service.create_skill(
        db, name="部署技能", description="如何部署本项目",
        content="部署步骤：make up 然后 make migrate",
        trigger_conditions={"keywords": ["部署", "上线"]}, owner_id=owner.id,
    )

    hit = await memory_service.search_skills(
        db, profile_id=None, owner_id=owner.id, team_id=None, query="怎么部署这个项目",
    )
    miss = await memory_service.search_skills(
        db, profile_id=None, owner_id=owner.id, team_id=None, query="今天天气怎么样",
    )

    assert len(hit) == 1
    assert hit[0].name == "部署技能"
    assert miss == []


async def test_search_skills_always_flag(db):
    owner = await _mk_user(db, "sk-owner2@h.io")
    await memory_service.create_skill(
        db, name="始终注入", description="", content="每次都要遵守的规则",
        trigger_conditions={"always": True}, owner_id=owner.id,
    )
    hit = await memory_service.search_skills(
        db, profile_id=None, owner_id=owner.id, team_id=None, query="随便问点什么",
    )
    assert len(hit) == 1


async def test_search_skills_disabled_excluded(db):
    owner = await _mk_user(db, "sk-owner3@h.io")
    await memory_service.create_skill(
        db, name="已禁用", description="", content="不该出现",
        trigger_conditions={"always": True}, owner_id=owner.id, enabled=False,
    )
    hit = await memory_service.search_skills(
        db, profile_id=None, owner_id=owner.id, team_id=None, query="任意问题",
    )
    assert hit == []


async def test_search_skills_respects_limit(db):
    owner = await _mk_user(db, "sk-owner4@h.io")
    for i in range(5):
        await memory_service.create_skill(
            db, name=f"技能{i}", description="", content="内容",
            trigger_conditions={"always": True}, owner_id=owner.id,
        )
    hit = await memory_service.search_skills(
        db, profile_id=None, owner_id=owner.id, team_id=None, query="任意问题", limit=2,
    )
    assert len(hit) == 2


# ── wiring into dispatch()'s system_prompt builders ──

async def test_episodic_and_skills_prompts_injected_into_dispatch(db):
    owner = await _mk_user(db, "wire-owner@h.io")
    profile = await _mk_profile(db, name="编程助手")
    now = datetime.now(tz=timezone.utc)
    await memory_service.add_episode(
        db, owner.id, None, "Redis会话", "用户和AI讨论了Redis Stream的限流键设计", 200, now,
    )
    await memory_service.create_skill(
        db, name="限流技能", description="限流相关问题",
        content="回答限流问题时提醒检查 rl:msg:{user} 键",
        trigger_conditions={"keywords": ["限流"]}, owner_id=owner.id, profile_id=profile.id,
    )

    convo = await svc.create_conversation(
        db, owner.id, title=None, primary_agent_id="hermes", profile_id=str(profile.id),
    )

    _, agent_msg = await svc.dispatch(
        db, convo, "Redis 限流应该怎么设计", owner_id=owner.id, profile_id_override=str(profile.id),
    )

    assert agent_msg.role == "agent"
    # dispatch() doesn't expose the resolved system_prompt on the returned
    # Message (it's only sent to the runner), so assert against the builder
    # functions directly with the same inputs dispatch() used.
    episodic = await svc._build_episodic_memory_prompt(db, owner.id, "Redis 限流应该怎么设计")
    skills, matched_skill_ids = await svc._build_skills_prompt(db, profile, owner.id, "Redis 限流应该怎么设计")
    assert episodic and "Redis Stream" in episodic
    assert skills and "限流技能" in skills
    assert matched_skill_ids


async def test_episodic_injection_disabled_by_kill_switch(db, monkeypatch):
    from app.config import settings

    owner = await _mk_user(db, "kill-owner@h.io")
    now = datetime.now(tz=timezone.utc)
    await memory_service.add_episode(db, owner.id, None, "标题", "摘要内容用于测试关闭开关", 10, now)

    monkeypatch.setattr(settings, "memory_episodic_injection_enabled", False)
    convo = await svc.create_conversation(
        db, owner.id, title=None, primary_agent_id="hermes", profile_id=None,
    )
    # Should not raise and should not attempt retrieval — a smoke test that
    # dispatch() still completes normally with the flag off.
    _, agent_msg = await svc.dispatch(db, convo, "摘要内容用于测试关闭开关", owner_id=owner.id)
    assert agent_msg.role == "agent"
