"""conversations API — member list, consolidate, reactions, read receipts,
folder CRUD. Route-level coverage for paths the existing suites skip.
"""
from __future__ import annotations

import uuid

import pytest

from app.db.models.conversation import Conversation, Message
from app.db.models.user import User
from app.core.security import hash_password

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email: str) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


def _headers(user: User) -> dict:
    from app.core.security import create_token
    return {"Authorization": f"Bearer {create_token(str(user.id), 'access')[0]}"}


async def _mk_convo(client, headers, db, title="会话"):
    from app.services import conversation_service as svc
    c = await svc.create_conversation(db, None, title=title, primary_agent_id="hermes",
                                      profile_id=None)
    return c


# ── group members endpoint ──

async def test_get_members_and_add_agent(client, db):
    from app.services import conversation_service as svc
    owner = await _mk_user(db, "cv-mem@h.io")
    group = await svc.create_group(db, owner.id, title="成员群")
    h = _headers(owner)
    r = await client.get(f"/api/v1/conversations/{group.id}/members", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)

    # Add an AI member
    r = await client.post(f"/api/v1/conversations/{group.id}/members",
                          json={"agent_id": "coder"}, headers=h)
    assert r.status_code in (200, 201)
    r = await client.get(f"/api/v1/conversations/{group.id}/members", headers=h)
    assert any(m.get("agent_id") == "coder" for m in r.json())


# ── reactions ──

async def test_message_reactions(client, db):
    from app.services import conversation_service as svc
    owner = await _mk_user(db, "cv-react@h.io")
    convo = await svc.create_conversation(db, owner.id, title="反应会话",
                                          primary_agent_id="hermes", profile_id=None)
    msg = Message(conversation_id=convo.id, owner_id=owner.id, role="user",
                  content={"text": "好"}, status="complete")
    db.add(msg)
    await db.flush()

    r = await client.post(
        f"/api/v1/conversations/{convo.id}/messages/{msg.id}/reactions",
        json={"emoji": "👍"}, headers=_headers(owner))
    assert r.status_code == 200, r.text
    assert "👍" in r.json().get("reactions", {})


# ── read receipts ──

async def test_mark_read(client, db):
    from app.services import conversation_service as svc
    owner = await _mk_user(db, "cv-read@h.io")
    convo = await svc.create_conversation(db, owner.id, title="已读会话",
                                          primary_agent_id="hermes", profile_id=None)
    r = await client.post(f"/api/v1/conversations/{convo.id}/read", headers=_headers(owner))
    assert r.status_code == 200


# ── folders CRUD ──

async def test_folder_crud(client, auth_headers):
    r = await client.post("/api/v1/conversations/folders",
                          json={"name": "回归文件夹"}, headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    folder = r.json()
    fid = folder["id"] if isinstance(folder, dict) else folder[0]["id"]

    r = await client.patch(f"/api/v1/conversations/folders/{fid}",
                           json={"name": "改名文件夹"}, headers=auth_headers)
    assert r.status_code == 200

    r = await client.delete(f"/api/v1/conversations/folders/{fid}", headers=auth_headers)
    assert r.status_code in (200, 204)


# ── consolidate via API ──

async def test_consolidate_api(client, db):
    from app.services import conversation_service as svc
    from app.services import team_service
    owner = await _mk_user(db, "cv-cons@h.io")
    convo = await svc.create_conversation(db, owner.id, title="沉淀会话",
                                          primary_agent_id="hermes", profile_id=None)
    msg = Message(conversation_id=convo.id, owner_id=owner.id, role="agent",
                  agent_id="hermes", content={"text": "沉淀正文内容"}, status="complete")
    db.add(msg)
    await db.flush()

    team = await team_service.create_team(db, owner, name="沉淀团队", handle="sdt",
                                          tagline="", color=None)
    r = await client.post(
        f"/api/v1/conversations/{convo.id}/messages/{msg.id}/consolidate",
        json={"target": "team_knowledge", "name": "API沉淀", "team_id": str(team.id)},
        headers=_headers(owner))
    assert r.status_code == 200, r.text
