"""team_service unit tests — direct service-layer coverage for the branches
the API-level tests don't reach (membership guards, member role updates,
project/task lifecycle, knowledge CRUD, activity logging, progress).
"""
from __future__ import annotations

import uuid

import pytest

from app.db.models.team import (
    Team,
)
from app.db.models.user import User
from app.services import team_service as svc
from app.core.security import hash_password

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email: str, role: str = "member") -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role=role,
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_team(db, owner: User, name: str = "服务层团队") -> Team:
    team = await svc.create_team(db, owner, name=name, handle=name, tagline="", color=None)
    await db.flush()
    return team


# ── membership guards ──

async def test_get_membership_and_require(db):
    owner = await _mk_user(db, "ts-owner@h.io", role="admin")
    team = await _mk_team(db, owner)
    m = await svc.get_membership(db, team.id, owner.id)
    assert m is not None and m.role == "owner"
    rm = await svc.require_membership(db, team.id, owner.id)
    assert rm[0].id == team.id

    outsider = await _mk_user(db, "ts-outsider@h.io")
    assert await svc.get_membership(db, team.id, outsider.id) is None
    import pytest as _pt
    with _pt.raises(Exception):
        await svc.require_membership(db, team.id, outsider.id)


async def test_require_permission_denies_without_role(db):
    owner = await _mk_user(db, "ts-perm@h.io", role="admin")
    team = await _mk_team(db, owner)
    # member can upload (policy allows) — the guard must pass for owner too
    await svc.require_permission(db, team.id, owner.id, "knowledge.upload")
    # knowledge.delete is owner/admin-only in the default policy
    member = await _mk_user(db, "ts-perm-m@h.io")
    await svc.add_member(db, team.id, member.email, role="viewer")
    import pytest as _pt
    with _pt.raises(Exception):
        await svc.require_permission(db, team.id, member.id, "knowledge.delete")


# ── member lifecycle ──

async def test_add_member_and_role_update_and_remove(db):
    owner = await _mk_user(db, "ts-mem@h.io", role="admin")
    team = await _mk_team(db, owner)
    member = await _mk_user(db, "ts-mem2@h.io")

    m = await svc.add_member(db, team.id, member.email, role="member")
    assert m.role == "member" and m.user_id == member.id

    updated = await svc.update_member_role(db, team.id, member.id, "admin")
    assert updated.role == "admin"

    await svc.remove_member(db, team.id, member.id)
    assert await svc.get_membership(db, team.id, member.id) is None

    # list_members returns (member, user) pairs
    pairs = await svc.list_members(db, team.id)
    assert any(u.id == owner.id for _, u in pairs)


async def test_add_member_nonexistent_email_raises(db):
    owner = await _mk_user(db, "ts-addr@h.io", role="admin")
    team = await _mk_team(db, owner)
    import pytest as _pt
    with _pt.raises(Exception):
        await svc.add_member(db, team.id, "nobody@h.io", role="member")


# ── project & task lifecycle ──

async def test_project_crud_and_tasks(db):
    owner = await _mk_user(db, "ts-proj@h.io", role="admin")
    team = await _mk_team(db, owner)

    class _Data:
        name = "项目A"
        handle = None
        color = None
        icon = "sparkle"
        summary = ""
        sections = []
        pinned_profile_ids = []
        deadline = None

    project = await svc.create_project(db, team.id, _Data(), owner=owner)
    assert project.name == "项目A" and project.team_id == team.id

    assert await svc.get_project(db, project.id) is not None
    assert await svc.get_project(db, uuid.uuid4()) is None
    projects = await svc.list_projects(db, team.id)
    assert any(p.id == project.id for p in projects)

    task = await svc.create_task(db, project.id, type("T", (), {"title": "任务1"})())
    assert task.title == "任务1" and task.project_id == project.id
    tasks = await svc.list_tasks(db, project.id)
    assert any(t.id == task.id for t in tasks)

    moved = await svc.move_task_status(db, task, "done", actor=owner)
    assert moved.status == "done"


async def test_recompute_progress(db):
    owner = await _mk_user(db, "ts-prog@h.io", role="admin")
    team = await _mk_team(db, owner)
    project = await svc.create_project(
        db, team.id, type("D", (), {"name": "进度项目", "handle": None, "color": None,
                                     "icon": "sparkle", "summary": "", "sections": [],
                                     "pinned_profile_ids": [], "deadline": None}),
        owner=owner,
    )
    await svc.create_task(db, project.id, type("T", (), {"title": "t1"}))
    await svc.create_task(db, project.id, type("T", (), {"title": "t2"}))
    done = await svc.create_task(db, project.id, type("T", (), {"title": "t3"}))
    await svc.move_task_status(db, done, "done", actor=owner)
    pct = await svc.recompute_progress(db, project)
    assert pct > 0 and pct < 100


async def test_activity_log_and_notify(db):
    owner = await _mk_user(db, "ts-act@h.io", role="admin")
    team = await _mk_team(db, owner)
    project = await svc.create_project(
        db, team.id, type("D", (), {"name": "活动项目", "handle": None, "color": None,
                                     "icon": "sparkle", "summary": "", "sections": [],
                                     "pinned_profile_ids": [], "deadline": None}),
        owner=owner,
    )
    await svc.log_activity(db, project=project, actor=owner, kind="task.derived",
                           summary="生成任务", meta={"n": 1})
    rows = await svc.list_project_activity(db, project.id)
    assert any(a.summary == "生成任务" for a in rows)


# ── knowledge ──

async def test_knowledge_add_update_delete(db):
    owner = await _mk_user(db, "ts-kb@h.io", role="admin")
    team = await _mk_team(db, owner)

    entry = await svc.add_knowledge(db, team.id, type("K", (), {
        "name": "文档.md", "kind": "md", "content": "内容", "folder_id": None,
        "size_bytes": 12,
    })(), user=owner)
    assert entry.name == "文档.md"

    rows = await svc.list_knowledge(db, team.id)
    assert any(k.id == entry.id for k in rows)

    from app.schemas.team import KnowledgeUpdate
    updated = await svc.update_knowledge(
        db, team.id, entry.id,
        KnowledgeUpdate(name="改名.md", content="新内容"),
    )
    assert updated is not None and updated.name == "改名.md"

    await svc.delete_knowledge(db, team.id, entry.id)
    rows = await svc.list_knowledge(db, team.id)
    assert all(k.id != entry.id for k in rows)


async def test_shared_profiles(db):
    owner = await _mk_user(db, "ts-sp@h.io", role="admin")
    team = await _mk_team(db, owner)
    team = await svc.set_shared_profiles(db, team, ["p1", "p2"])
    assert "p1" in (team.shared_profile_ids or []) and "p2" in team.shared_profile_ids


async def test_cleanup_storage_key_handles_missing(db):
    # Nonexistent key must not raise — best-effort cleanup
    await svc.cleanup_storage_key("e2e-non-existent-key-xyz")
