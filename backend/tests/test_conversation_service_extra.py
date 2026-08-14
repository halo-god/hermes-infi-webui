"""conversation_service extra branches — consolidate, auto tasks, project
group creation, summary trigger. Service-level coverage for the paths the
API tests don't reach.
"""
from __future__ import annotations

import uuid

import pytest

from app.db.models.conversation import Conversation, Message
from app.db.models.team import TeamMember
from app.db.models.user import User
from app.core.security import hash_password
from app.services import conversation_service as svc
from app.services import team_service

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email: str) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_convo(db, owner: User) -> Conversation:
    c = Conversation(owner_id=owner.id, title="会话", primary_agent_id="hermes")
    db.add(c)
    await db.flush()
    return c


# ── consolidate_message ──

async def test_consolidate_to_project_doc(db):
    owner = await _mk_user(db, "cs-cons@h.io")
    convo = await _mk_convo(db, owner)
    msg = Message(
        conversation_id=convo.id, role="agent", agent_id="hermes",
        content={"text": "这是要沉淀的内容"}, status="complete",
    )
    db.add(msg)
    await db.flush()

    team = await team_service.create_team(db, owner, name="沉淀团队", handle="ct",
                                          tagline="", color=None)
    project = await team_service.create_project(
        db, team.id,
        type("D", (), {"name": "沉淀项目", "handle": None, "color": None,
                       "icon": "sparkle", "summary": "", "sections": [],
                       "pinned_profile_ids": [], "deadline": None}),
        owner=owner,
    )
    doc = await svc.consolidate_message(
        db, message=msg, target="project_doc", name="沉淀文档",
        actor=owner, project=project, team=team,
    )
    assert doc is not None
    assert doc.name == "沉淀文档"


async def test_consolidate_to_team_knowledge(db):
    owner = await _mk_user(db, "cs-cons2@h.io")
    convo = await _mk_convo(db, owner)
    msg = Message(
        conversation_id=convo.id, role="agent", agent_id="hermes",
        content={"text": "知识内容"}, status="complete",
    )
    db.add(msg)
    await db.flush()
    team = await team_service.create_team(db, owner, name="知识团队", handle="kt",
                                          tagline="", color=None)
    entry = await svc.consolidate_message(
        db, message=msg, target="team_knowledge", name="知识条目",
        actor=owner, team=team,
    )
    assert entry is not None
    assert entry.name == "知识条目"


# ── auto_create_tasks_from_message ──

async def test_auto_create_tasks_parses_bullets(db):
    owner = await _mk_user(db, "cs-tasks@h.io")
    convo = await _mk_convo(db, owner)
    msg = Message(
        conversation_id=convo.id, role="agent", agent_id="hermes",
        content={"text": "行动项：\n- 完成登录页\n- 修复缓存\n- 写测试"}, status="complete",
    )
    db.add(msg)
    await db.flush()
    team = await team_service.create_team(db, owner, name="任务团队", handle="tt",
                                          tagline="", color=None)
    project = await team_service.create_project(
        db, team.id,
        type("D", (), {"name": "任务项目", "handle": None, "color": None,
                       "icon": "sparkle", "summary": "", "sections": [],
                       "pinned_profile_ids": [], "deadline": None}),
        owner=owner,
    )
    tasks = await svc.auto_create_tasks_from_message(
        db, message=msg, project=project, actor=owner,
    )
    assert len(tasks) >= 1
    rows = await team_service.list_tasks(db, project.id)
    assert len(rows) >= 1


# ── get_or_create_project_group ──

async def test_project_group_created_once(db):
    owner = await _mk_user(db, "cs-pg@h.io")
    team = await team_service.create_team(db, owner, name="项目群团队", handle="pgt",
                                          tagline="", color=None)
    project = await team_service.create_project(
        db, team.id,
        type("D", (), {"name": "群项目", "handle": None, "color": None,
                       "icon": "sparkle", "summary": "", "sections": [],
                       "pinned_profile_ids": [], "deadline": None}),
        owner=owner,
    )
    g1 = await svc.get_or_create_project_group(db, project, owner.id)
    assert g1.type == "group" and g1.project_id == project.id
    g2 = await svc.get_or_create_project_group(db, project, owner.id)
    assert g1.id == g2.id, "must return the same group"


# ── _maybe_trigger_summary ──

async def test_summary_trigger_below_threshold_noop(db, monkeypatch):
    """Short conversations must not enqueue a summary task."""
    from app.config import settings
    from app.core import redis as redis_core
    owner = await _mk_user(db, "cs-sum@h.io")
    convo = await _mk_convo(db, owner)
    monkeypatch.setattr(settings, "summary_enabled", True)
    monkeypatch.setattr(settings, "summary_trigger_msg_count", 50)
    calls = []
    async def _enqueue(payload):
        calls.append(payload)
        return "1"
    monkeypatch.setattr(redis_core, "enqueue_prompt", _enqueue)
    await svc._maybe_trigger_summary(db, convo.id)
    assert calls == [], "short conversation must not enqueue summary"


# ── group member removal + sync ──

async def test_remove_group_member_and_sync(db):
    owner = await _mk_user(db, "cs-rm@h.io")
    member = await _mk_user(db, "cs-rm2@h.io")
    team = await team_service.create_team(db, owner, name="移除团队", handle="rmt",
                                          tagline="", color=None)
    db.add(TeamMember(team_id=team.id, user_id=member.id, role="member"))
    await db.flush()

    group = await svc.create_group(db, owner.id, title="移除群", team_id=team.id)
    assert await svc.is_group_admin(db, group.id, owner.id)

    # Remove the member from the team → group membership must follow
    await team_service.remove_member(db, team.id, member.id)
    await svc.sync_group_membership(
        db, group, human_user_ids=[owner.id], agent_ids=[],
    )
    members = await svc.get_group_members(db, group.id)
    assert all(m.user_id != member.id for m in members)


async def test_remove_group_member_directly(db):
    owner = await _mk_user(db, "cs-rm3@h.io")
    member = await _mk_user(db, "cs-rm4@h.io")
    group = await svc.create_group(db, owner.id, title="直删群", member_user_ids=[member.id])
    await svc.remove_group_member(db, group.id, user_id=member.id)
    members = await svc.get_group_members(db, group.id)
    assert all(m.user_id != member.id for m in members)
