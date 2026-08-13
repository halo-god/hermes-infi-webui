"""Boundary-scenario tests from the second review round.

Covers: pagination cursor key consistency, fork cut-point validation +
tombstone propagation, share/unshare lifecycle + sanitized shared view,
recursive knowledge-folder deletion, and recalled-message unread exclusion.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.db.models.conversation import Conversation, Message
from app.db.models.team import Team, TeamKnowledge, TeamMember
from app.db.models.user import User
from app.services import conversation_service as svc


async def _mk_user(db, email: str) -> User:
    from app.core.security import hash_password
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_convo(db, owner: User, title: str = "边界测试") -> Conversation:
    c = Conversation(owner_id=owner.id, title=title, primary_agent_id="hermes")
    db.add(c)
    await db.flush()
    return c


# ── Pagination cursor key consistency ───────────────────────────────────


@pytest.mark.asyncio
async def test_pagination_no_dup_or_skip_same_timestamp_pairs(db):
    """Messages sharing one created_at (user/agent pairs written in the same
    transaction) must not be duplicated or skipped across pages — the cursor
    key (created_at, id) and the ORDER BY must agree."""
    owner = await _mk_user(db, "page1@h.io")
    convo = await _mk_convo(db, owner)
    ts = datetime.now(timezone.utc)
    msgs = []
    for i in range(6):
        msgs.append(Message(
            conversation_id=convo.id, owner_id=owner.id, role="user",
            content={"text": f"u{i}"}, status="complete", created_at=ts,
        ))
        msgs.append(Message(
            conversation_id=convo.id, role="agent", agent_id="hermes",
            content={"text": f"a{i}"}, status="complete", created_at=ts,
        ))
    db.add_all(msgs)
    await db.flush()

    # Page 1: last 5 messages (limit 5).
    page1 = await svc.get_messages(db, convo.id, limit=5)
    assert len(page1) == 5
    # Page 2: cursor = oldest of page 1.
    cursor = page1[0].id
    page2 = await svc.get_messages(db, convo.id, limit=5, before_id=cursor)
    ids1 = {str(m.id) for m in page1}
    ids2 = {str(m.id) for m in page2}
    assert not (ids1 & ids2), "pages overlap"
    # Page 3: the remaining 2 messages must still be reachable — walking all
    # three pages must yield exactly the 12 messages, none duplicated, none
    # skipped (the old (created_at, role) ORDER BY dropped pairs sharing a
    # timestamp).
    page3 = await svc.get_messages(db, convo.id, limit=5, before_id=page2[0].id)
    ids3 = {str(m.id) for m in page3}
    assert not (ids2 & ids3), "pages 2/3 overlap"
    assert len(ids1) + len(ids2) + len(ids3) == 12, "messages skipped across pages"


# ── Fork cut-point validation + tombstone propagation ───────────────────


@pytest.mark.asyncio
async def test_fork_rejects_foreign_or_missing_cut_message(db):
    owner = await _mk_user(db, "fork1@h.io")
    convo = await _mk_convo(db, owner)
    other = await _mk_convo(db, owner, "其他会话")
    db.add(Message(
        conversation_id=other.id, owner_id=owner.id, role="user",
        content={"text": "x"}, status="complete",
    ))
    await db.flush()
    other_msg = (await db.execute(
        __import__("sqlalchemy").select(Message).where(Message.conversation_id == other.id)
    )).scalars().first()

    with pytest.raises(ValueError):
        await svc.fork_conversation(db, convo.id, owner.id, other_msg.id)
    with pytest.raises(ValueError):
        await svc.fork_conversation(db, convo.id, owner.id, uuid.uuid4())


@pytest.mark.asyncio
async def test_fork_propagates_tombstone_and_mentions(db):
    owner = await _mk_user(db, "fork2@h.io")
    convo = await _mk_convo(db, owner)
    ts = datetime.now(timezone.utc)
    u = Message(
        conversation_id=convo.id, owner_id=owner.id, role="user",
        content={"text": "你好"}, status="complete",
        mentions=["user:someone"], created_at=ts,
    )
    a = Message(
        conversation_id=convo.id, role="agent", agent_id="hermes",
        content={"text": "已撤回"}, status="complete",
        deleted_at=datetime.now(timezone.utc), created_at=ts,
    )
    db.add_all([u, a])
    await db.flush()

    fork, copied = await svc.fork_conversation(db, convo.id, owner.id, a.id)
    assert len(copied) == 2
    by_role = {m.role: m for m in copied}
    assert by_role["user"].mentions == ["user:someone"], "mentions lost in fork"
    assert by_role["agent"].deleted_at is not None, "recalled message resurrected in fork!"


# ── Share / unshare lifecycle (API) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_share_unshare_lifecycle(client, auth_headers):
    r = await client.post("/api/v1/conversations", json={
        "title": "分享测试", "primary_agent_id": "hermes",
    }, headers=auth_headers)
    cid = r.json()["id"]

    s = await client.post(f"/api/v1/conversations/{cid}/share", headers=auth_headers)
    assert s.status_code == 200
    shared = await client.get(f"/api/v1/conversations/shared/{cid}")
    assert shared.status_code == 200

    # Unshare → the shared URL immediately 404s.
    u = await client.delete(f"/api/v1/conversations/{cid}/share", headers=auth_headers)
    assert u.status_code == 204
    gone = await client.get(f"/api/v1/conversations/shared/{cid}")
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_shared_view_sanitizes_internal_metadata(client, auth_headers, test_user, db):
    from app.db.models.conversation import Message
    r = await client.post("/api/v1/conversations", json={
        "title": "分享清洗", "primary_agent_id": "hermes",
    }, headers=auth_headers)
    cid = r.json()["id"]
    m = Message(
        conversation_id=uuid.UUID(cid), owner_id=test_user.id, role="user",
        content={
            "text": "可见正文",
            "files": [{"id": "f1", "name": "报告.pdf", "kind": "pdf"}],
            "knowledge_refs": [{"id": "k1", "name": "内部产品手册", "team_id": "t1"}],
            "rag_refs": [{"n": 1, "source_name": "机密来源.docx"}],
        },
        status="complete",
    )
    db.add(m)
    await db.commit()

    await client.post(f"/api/v1/conversations/{cid}/share", headers=auth_headers)
    shared = (await client.get(f"/api/v1/conversations/shared/{cid}")).json()
    content = shared["messages"][0]["content"]
    assert content["text"] == "可见正文"
    assert content["files"][0]["name"] == "报告.pdf"
    assert "knowledge_refs" not in content, "team knowledge names leaked to anonymous viewer"
    assert "rag_refs" not in content, "RAG source names leaked to anonymous viewer"
    assert "mentions" not in content and "reactions" not in content


# ── Recursive knowledge folder deletion ─────────────────────────────────


@pytest.mark.asyncio
async def test_delete_knowledge_folder_recurses(db):
    from app.services import team_service
    owner = await _mk_user(db, "kf1@h.io")
    team = Team(id=uuid.uuid4(), name="文件夹团队", channel_mode="mention")
    db.add(team)
    await db.flush()
    db.add(TeamMember(team_id=team.id, user_id=owner.id, role="owner"))

    folder = TeamKnowledge(
        team_id=team.id, name="旧文档", kind="folder", is_folder=True,
        size_bytes=0, uploaded_by=owner.id,
    )
    db.add(folder)
    await db.flush()
    child = TeamKnowledge(
        team_id=team.id, name="child.md", kind="md", content="# 子文件",
        folder_id=folder.id, size_bytes=5, uploaded_by=owner.id,
    )
    db.add(child)
    await db.flush()

    await team_service.delete_knowledge(db, team.id, folder.id)
    from sqlalchemy import select
    remaining = (await db.execute(
        select(TeamKnowledge).where(TeamKnowledge.team_id == team.id)
    )).scalars().all()
    assert remaining == [], "folder children left dangling after folder delete"


# ── Recalled messages excluded from unread ──────────────────────────────


@pytest.mark.asyncio
async def test_recalled_message_not_counted_unread(db):
    owner = await _mk_user(db, "unr1@h.io")
    member = await _mk_user(db, "unr2@h.io")
    team = Team(id=uuid.uuid4(), name="未读团队", channel_mode="mention")
    db.add(team)
    await db.flush()
    db.add(TeamMember(team_id=team.id, user_id=owner.id, role="owner"))
    db.add(TeamMember(team_id=team.id, user_id=member.id, role="member"))

    # get_or_create_team_group syncs team members into the group automatically.
    group = await svc.get_or_create_team_group(db, team, owner.id)
    await db.flush()

    alive = Message(
        conversation_id=group.id, owner_id=owner.id, role="user",
        content={"text": "正常消息"}, status="complete",
    )
    recalled = Message(
        conversation_id=group.id, owner_id=owner.id, role="user",
        content={"text": ""}, status="complete",
        deleted_at=datetime.now(timezone.utc),
    )
    db.add_all([alive, recalled])
    await db.commit()

    summary = await svc.unread_summary(db, member.id, [group.id])
    assert summary[str(group.id)]["unread"] == 1, "recalled message counted as unread"


# ── Enqueue failure closes out streaming placeholders ────────────────────


@pytest.mark.asyncio
async def test_send_message_enqueue_failure_flips_agent_msg_to_error(db, monkeypatch):
    """send_message commits a "streaming" agent row BEFORE enqueuing — if the
    Redis enqueue fails, the row must flip to error or the UI would show an
    endless spinner for a turn the runner never receives."""
    from sqlalchemy import select

    owner = await _mk_user(db, "enfail1@h.io")
    convo = await _mk_convo(db, owner)

    async def _boom(payload):
        raise ConnectionError("redis down")

    monkeypatch.setattr(svc.redis_core, "enqueue_prompt", _boom)
    with pytest.raises(ConnectionError):
        await svc.send_message(db, convo, "你好")

    msgs = (await db.execute(
        select(Message).where(Message.conversation_id == convo.id)
    )).scalars().all()
    agent = [m for m in msgs if m.role == "agent"][0]
    assert agent.status == "error"
    assert "服务暂不可用" in (agent.content or {}).get("error", "")


@pytest.mark.asyncio
async def test_send_roundtable_enqueue_failure_flips_rt_msg_to_error(db, monkeypatch):
    from sqlalchemy import select

    owner = await _mk_user(db, "enfail2@h.io")
    convo = await _mk_convo(db, owner)
    targets = [{"agent_id": "hermes", "profile_id": None, "system_prompt": "", "profile_dir": None}]

    async def _boom(payload):
        raise ConnectionError("redis down")

    monkeypatch.setattr(svc.redis_core, "enqueue_prompt", _boom)
    with pytest.raises(ConnectionError):
        await svc.send_roundtable(db, convo, "你好", targets, owner_id=owner.id)

    msgs = (await db.execute(
        select(Message).where(Message.conversation_id == convo.id)
    )).scalars().all()
    rt = [m for m in msgs if m.role == "roundtable"][0]
    assert rt.status == "error"
    assert "服务暂不可用" in (rt.content or {}).get("error", "")


@pytest.mark.asyncio
async def test_send_chain_enqueue_failure_flips_chain_msg_to_error(db, monkeypatch):
    from sqlalchemy import select

    owner = await _mk_user(db, "enfail3@h.io")
    convo = await _mk_convo(db, owner)
    targets = [{"agent_id": "hermes", "profile_id": None, "system_prompt": "", "profile_dir": None}]

    async def _boom(payload):
        raise ConnectionError("redis down")

    monkeypatch.setattr(svc.redis_core, "enqueue_prompt", _boom)
    with pytest.raises(ConnectionError):
        await svc.send_chain(db, convo, "你好", targets, owner_id=owner.id)

    msgs = (await db.execute(
        select(Message).where(Message.conversation_id == convo.id)
    )).scalars().all()
    chain = [m for m in msgs if m.role == "chain"][0]
    assert chain.status == "error"
    assert "服务暂不可用" in (chain.content or {}).get("error", "")
