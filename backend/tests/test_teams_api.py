"""Teams API — create/update/members/invite/knowledge/projects.

teams.py is the largest router (~57 endpoints) but had only 5 tests.
These cover the user-visible team basics with the rolled-back client
fixture (no real teams leak into the dev DB).
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


PREFIX = "/api/v1/teams"


async def _mk_team(client, headers, name: str | None = None) -> dict:
    r = await client.post(PREFIX, json={
        "name": name or f"测试团队-{uuid.uuid4().hex[:6]}",
        "tagline": "E2E 测试团队",
    }, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _mk_member_user(db, email: str):
    from app.core.security import hash_password
    from app.db.models.user import User
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


# ── team CRUD ──

async def test_create_team(client, admin_headers):
    team = await _mk_team(client, admin_headers, "E2E创建团队")
    assert team["name"] == "E2E创建团队"
    assert team["plan"] in ("team", "business")
    # Creator becomes owner member automatically
    r = await client.get(f"{PREFIX}/{team['id']}/members", headers=admin_headers)
    assert r.status_code == 200
    members = r.json() if isinstance(r.json(), list) else r.json().get("members", [])
    assert any(m.get("role") == "owner" for m in members)


async def test_create_team_requires_admin(client, auth_headers):
    """team.create is admin-only — a plain member gets 403."""
    r = await client.post(PREFIX, json={"name": "越权团队"}, headers=auth_headers)
    assert r.status_code == 403


async def test_update_team(client, admin_headers):
    team = await _mk_team(client, admin_headers)
    r = await client.patch(f"{PREFIX}/{team['id']}", json={
        "name": "改名团队", "tagline": "新标语",
    }, headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["name"] == "改名团队"
    assert r.json()["tagline"] == "新标语"


async def test_delete_team(client, admin_headers):
    team = await _mk_team(client, admin_headers)
    r = await client.delete(f"{PREFIX}/{team['id']}", headers=admin_headers)
    assert r.status_code == 204
    r = await client.get(f"{PREFIX}/{team['id']}", headers=admin_headers)
    assert r.status_code == 404


# ── members & invite ──

async def test_invite_member_and_join_by_token(client, admin_headers, db):
    from app.core.security import create_token
    team = await _mk_team(client, admin_headers)
    newbie = await _mk_member_user(db, "team-join@h.io")
    newbie_headers = {"Authorization": f"Bearer {create_token(str(newbie.id), 'access')[0]}"}

    # Admin generates invite token
    r = await client.post(f"{PREFIX}/{team['id']}/invite-token",
                          json={"role": "member"}, headers=admin_headers)
    assert r.status_code == 200
    token = r.json().get("token") or r.json().get("invite_token")
    assert token, r.text

    # Newbie joins via token
    r = await client.post(f"{PREFIX}/join-by-token", json={"token": token},
                          headers=newbie_headers)
    assert r.status_code == 200, r.text

    # Newbie is now a member
    r = await client.get(f"{PREFIX}/{team['id']}/members", headers=newbie_headers)
    assert r.status_code == 200
    members = r.json() if isinstance(r.json(), list) else r.json().get("members", [])
    assert any(m.get("user_id") == str(newbie.id) or m.get("id") == str(newbie.id)
               for m in members)


async def test_member_cannot_invite(client, admin_headers, db):
    """team.invite needs admin/team_admin — a plain member gets 403."""
    from app.core.security import create_token
    team = await _mk_team(client, admin_headers)
    member = await _mk_member_user(db, "team-noinvite@h.io")
    member_headers = {"Authorization": f"Bearer {create_token(str(member.id), 'access')[0]}"}
    r = await client.post(f"{PREFIX}/{team['id']}/invite-token",
                          json={"role": "member"}, headers=member_headers)
    assert r.status_code == 403


# ── knowledge base ──

async def test_knowledge_json_create_and_folder_move(client, admin_headers):
    team = await _mk_team(client, admin_headers)
    # Create a knowledge folder
    r = await client.post(f"{PREFIX}/{team['id']}/knowledge/folder",
                          json={"name": "资料夹"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    folder = r.json()

    # Create a plain knowledge entry (JSON body)
    r = await client.post(f"{PREFIX}/{team['id']}/knowledge", json={
        "name": "会议纪要.md", "kind": "md", "content": "第一条知识内容",
    }, headers=admin_headers)
    assert r.status_code == 201, r.text
    entry = r.json()

    # Move into folder
    r = await client.patch(f"{PREFIX}/{team['id']}/knowledge/{entry['id']}/move",
                           json={"folder_id": folder["id"]}, headers=admin_headers)
    assert r.status_code == 200, r.text

    # List (recursive=true returns the whole tree incl. folder contents)
    r = await client.get(f"{PREFIX}/{team['id']}/knowledge?recursive=true",
                         headers=admin_headers)
    assert r.status_code == 200
    names = [k["name"] for k in r.json()]
    assert "会议纪要.md" in names and "资料夹" in names


async def test_knowledge_upload_multipart(client, admin_headers):
    """The upload endpoint (Docling pipeline) must accept a real file and
    return a ready entry."""
    team = await _mk_team(client, admin_headers)
    r = await client.post(
        f"{PREFIX}/{team['id']}/knowledge/upload",
        files={"file": ("test-kb.md", "# E2E 知识库\n内容".encode(), "text/markdown")},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "test-kb.md"
    assert data["processing_status"] in ("ready", "processing")


async def test_knowledge_delete(client, admin_headers):
    team = await _mk_team(client, admin_headers)
    r = await client.post(f"{PREFIX}/{team['id']}/knowledge", json={
        "name": "待删除条目", "content": "x",
    }, headers=admin_headers)
    kid = r.json()["id"]
    r = await client.delete(f"{PREFIX}/{team['id']}/knowledge/{kid}", headers=admin_headers)
    assert r.status_code == 204
    r = await client.get(f"{PREFIX}/{team['id']}/knowledge", headers=admin_headers)
    assert all(k["id"] != kid for k in r.json())


# ── projects & tasks ──

async def test_project_create_and_task(client, admin_headers):
    team = await _mk_team(client, admin_headers)
    r = await client.post(f"{PREFIX}/{team['id']}/projects",
                          json={"name": "E2E项目"}, headers=admin_headers)
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["name"] == "E2E项目"

    r = await client.get(f"{PREFIX}/{team['id']}/projects", headers=admin_headers)
    assert any(p["id"] == project["id"] for p in r.json())

    # Task inside the project
    r = await client.post(f"/api/v1/projects/{project['id']}/tasks",
                          json={"title": "写测试"}, headers=admin_headers)
    assert r.status_code in (200, 201), r.text
    task = r.json()
    assert task["title"] == "写测试"

    # Move task status
    r = await client.patch(f"/api/v1/tasks/{task['id']}/status",
                           json={"status": "done"}, headers=admin_headers)
    assert r.status_code == 200, r.text


async def test_project_list_requires_membership(client, admin_headers, db):
    """A user outside the team must not see its projects."""
    from app.core.security import create_token
    team = await _mk_team(client, admin_headers)
    await client.post(f"{PREFIX}/{team['id']}/projects",
                      json={"name": "内部项目"}, headers=admin_headers)
    outsider = await _mk_member_user(db, "team-outsider@h.io")
    outsider_headers = {"Authorization": f"Bearer {create_token(str(outsider.id), 'access')[0]}"}
    r = await client.get(f"{PREFIX}/{team['id']}/projects", headers=outsider_headers)
    assert r.status_code in (403, 404), "outsider must not list team projects"
