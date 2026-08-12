"""Knowledge reference + file attachment injection tests (single chat).

Covers the two reference channels a user message can carry:

- attached_file_ids → ACP task content_blocks (resource_link) + inline
  prompt reference + the attachment written into the conversation's
  workspace attachments dir (so the agent can read_file it).
- knowledge_ids → <knowledge> block injected into system_prompt + the user
  message's knowledge_refs metadata for the UI.
- SECURITY: knowledge_ids pointing at items from teams the caller does NOT
  belong to are filtered out — no cross-team content exfiltration via
  forged knowledge_ids.
"""
import json
import os
import uuid

import pytest

from app.config import settings
from app.core import redis as redis_core
from app.db.models.team import Team, TeamKnowledge, TeamMember
from app.db.models.user import User
from app.db.models.workspace import WorkspaceFile
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


async def _mk_convo(db, owner: User, title: str = "单聊"):
    from app.db.models.conversation import Conversation
    c = Conversation(owner_id=owner.id, title=title, primary_agent_id="hermes")
    db.add(c)
    await db.flush()
    return c


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Point workspace_root at a temp dir so attachment writes are isolated
    AND assertable (the real default writes into the developer's home)."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    return tmp_path


async def _last_task():
    r = redis_core.get_redis()
    entries = await r.xrange(settings.acp_stream, "-", "+")
    assert entries, "no ACP task enqueued"
    return json.loads(entries[-1][1]["data"])


@pytest.mark.asyncio
async def test_single_chat_attachment_lands_in_task_and_workspace(db, tmp_workspace):
    """A 1:1 chat turn with an attachment must enqueue an ACP task carrying a
    resource_link content block + inline reference, and the file must be
    physically written into the conversation's attachments dir."""
    owner = await _mk_user(db, "ref1@h.io")
    convo = await _mk_convo(db, owner)

    wf = WorkspaceFile(
        conversation_id=convo.id, name="notes.md", kind="md",
        content="# 笔记内容", size_bytes=10,
    )
    db.add(wf)
    await db.flush()

    await svc.dispatch(
        db, convo, "请分析这个文件", attached_file_ids=[str(wf.id)], owner_id=owner.id,
    )

    task = await _last_task()
    assert task["type"] == "single"
    blocks = task.get("content_blocks") or []
    rl = next((b for b in blocks if b.get("type") == "resource_link"), None)
    assert rl is not None, blocks
    assert rl["name"] == "notes.md"
    assert rl["uri"].startswith("file://")
    # Inline prompt reference mentions the file.
    assert "notes.md" in task["text"]

    # Attachment physically present in the workspace dir for read_file.
    ws_rel = rl["uri"].replace("file://", "")
    assert os.path.isfile(ws_rel), "attachment not written to workspace dir"
    with open(ws_rel, encoding="utf-8") as fh:
        assert "笔记内容" in fh.read()


@pytest.mark.asyncio
async def test_single_chat_knowledge_ids_inject_prompt(db):
    """Request-level knowledge_ids must inject the item's content into the
    task system_prompt and record knowledge_refs on the user message."""
    owner = await _mk_user(db, "ref2@h.io")
    team = Team(id=uuid.uuid4(), name="KB 团队", channel_mode="mention")
    db.add(team)
    await db.flush()
    db.add(TeamMember(team_id=team.id, user_id=owner.id, role="owner"))
    k = TeamKnowledge(
        team_id=team.id, name="产品手册", kind="md",
        content="# 产品功能\n这是核心功能说明。", size_bytes=20,
        uploaded_by=owner.id, uploaded_by_name="owner",
    )
    db.add(k)
    await db.flush()
    convo = await _mk_convo(db, owner)

    await svc.dispatch(
        db, convo, "根据知识库回答", knowledge_ids=[str(k.id)], owner_id=owner.id,
    )

    task = await _last_task()
    assert "<knowledge>" in (task.get("system_prompt") or "")
    assert "产品手册" in task["system_prompt"]
    assert "这是核心功能说明" in task["system_prompt"]

    # User message carries knowledge_refs metadata for the UI badges.
    from sqlalchemy import select
    from app.db.models.conversation import Message
    msg = (await db.execute(
        select(Message).where(Message.conversation_id == convo.id, Message.role == "user")
    )).scalars().first()
    assert msg is not None
    refs = (msg.content or {}).get("knowledge_refs") or []
    assert any(r.get("id") == str(k.id) for r in refs)


@pytest.mark.asyncio
async def test_knowledge_ids_from_other_team_filtered_out(db):
    """SECURITY: knowledge_ids pointing at another team's items must NOT leak
    content into the prompt — the caller is not a member of that team."""
    owner = await _mk_user(db, "ref3@h.io")
    alien = await _mk_user(db, "ref4@h.io")

    my_team = Team(id=uuid.uuid4(), name="我的团队", channel_mode="mention")
    alien_team = Team(id=uuid.uuid4(), name="他人团队", channel_mode="mention")
    db.add_all([my_team, alien_team])
    await db.flush()
    db.add(TeamMember(team_id=my_team.id, user_id=owner.id, role="owner"))
    db.add(TeamMember(team_id=alien_team.id, user_id=alien.id, role="owner"))

    mine = TeamKnowledge(
        team_id=my_team.id, name="我的资料", kind="md",
        content="我的秘密内容", size_bytes=10, uploaded_by=owner.id,
    )
    theirs = TeamKnowledge(
        team_id=alien_team.id, name="他人机密", kind="md",
        content="他人团队的机密内容", size_bytes=10, uploaded_by=alien.id,
    )
    db.add_all([mine, theirs])
    await db.flush()
    convo = await _mk_convo(db, owner)

    await svc.dispatch(
        db, convo, "回答我", knowledge_ids=[str(mine.id), str(theirs.id)], owner_id=owner.id,
    )

    task = await _last_task()
    prompt = task.get("system_prompt") or ""
    assert "我的秘密内容" in prompt
    assert "他人团队的机密内容" not in prompt, "cross-team knowledge leaked into prompt!"

    # knowledge_refs metadata must be filtered too — a forged id must not
    # even leak the other team's entry NAME into the persisted message.
    from sqlalchemy import select
    from app.db.models.conversation import Message
    msg = (await db.execute(
        select(Message).where(Message.conversation_id == convo.id, Message.role == "user")
    )).scalars().first()
    refs = (msg.content or {}).get("knowledge_refs") or []
    ids = [r.get("id") for r in refs]
    assert str(mine.id) in ids
    assert str(theirs.id) not in ids, "cross-team knowledge_refs leaked into message metadata!"
    assert not any(r.get("name") == "他人机密" for r in refs)


@pytest.mark.asyncio
async def test_request_knowledge_prompt_direct_filter(db):
    """Direct call: a non-member's knowledge id yields nothing (None)."""
    owner = await _mk_user(db, "ref5@h.io")
    alien = await _mk_user(db, "ref6@h.io")
    alien_team = Team(id=uuid.uuid4(), name="外人团队", channel_mode="mention")
    db.add(alien_team)
    await db.flush()
    db.add(TeamMember(team_id=alien_team.id, user_id=alien.id, role="owner"))
    k = TeamKnowledge(
        team_id=alien_team.id, name="外人机密", kind="md",
        content="外人内容", size_bytes=10, uploaded_by=alien.id,
    )
    db.add(k)
    await db.flush()

    out = await svc._build_request_knowledge_prompt(  # noqa: SLF001
        db, [str(k.id)], owner_id=owner.id,
    )
    assert out is None


@pytest.mark.asyncio
async def test_send_user_only_persists_attachments(db, tmp_workspace):
    """skip_agent (save-only) turns must persist the attached file metadata —
    attachments must not silently vanish when the agent is skipped."""
    owner = await _mk_user(db, "ref7@h.io")
    convo = await _mk_convo(db, owner)
    wf = WorkspaceFile(
        conversation_id=convo.id, name="notes.md", kind="md",
        content="# x", size_bytes=4,
    )
    db.add(wf)
    await db.flush()

    user_msg, agent_msg = await svc.send_user_only(
        db, convo, "记录一下", attached_file_ids=[str(wf.id)], owner_id=owner.id,
    )
    assert agent_msg is None
    files = (user_msg.content or {}).get("files") or []
    assert any(f.get("id") == str(wf.id) and f.get("name") == "notes.md" for f in files)


def test_truncate_knowledge_blocks_unified():
    """The single truncate entry point used by both HTTP/WS send and
    dispatch_group caps oversized <knowledge> blocks with a read_file hint."""
    big = "<knowledge>" + "x" * 150_000 + "</knowledge>"
    out = svc.truncate_knowledge_blocks(big)
    assert len(out) < 100_100
    assert "read_file" in out
    # Small blocks pass through untouched.
    small = "<knowledge>小</knowledge>"
    assert svc.truncate_knowledge_blocks(small) == small
