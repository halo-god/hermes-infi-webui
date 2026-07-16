"""Tests for the redesigned team/project group chat.

Covers mention resolution (stable IDs, no fuzzy matching), realtime peer
fan-out, unread/@-mention summary, canonical team groups, and message
edit/recall/reactions.
"""
import uuid

import pytest

from app.core import redis as redis_core
from app.db.models.team import Team, TeamMember
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


async def _mk_team_with_members(db, owner: User, others: list[User]) -> Team:
    team = Team(id=uuid.uuid4(), name="Squad", channel_mode="mention")
    db.add(team)
    await db.flush()
    db.add(TeamMember(team_id=team.id, user_id=owner.id, role="owner"))
    for u in others:
        db.add(TeamMember(team_id=team.id, user_id=u.id, role="member"))
    await db.flush()
    return team


@pytest.mark.asyncio
async def test_resolve_mentions_buckets(db):
    owner = await _mk_user(db, "owner@h.io")
    bob = await _mk_user(db, "bob@h.io")
    team = await _mk_team_with_members(db, owner, [bob])
    group = await svc.get_or_create_team_group(db, team, owner.id)

    # agent + human + all markers, all via stable IDs
    res = await svc.resolve_mentions(db, group.id, [
        "hermes", f"user:{bob.id}", "__all_humans__",
    ])
    assert res.agent_ids == ["hermes"]
    assert bob.id in res.user_ids
    assert res.all_humans is True
    assert res.all_agents is False

    # a non-member agent id is NOT trusted
    res2 = await svc.resolve_mentions(db, group.id, ["ghost-agent"])
    assert res2.agent_ids == []

    # __all_agents__ expands to group agents
    res3 = await svc.resolve_mentions(db, group.id, ["__all_agents__"])
    assert "hermes" in res3.agent_ids and res3.all_agents is True


@pytest.mark.asyncio
async def test_dispatch_group_save_only_broadcasts(db):
    owner = await _mk_user(db, "o2@h.io")
    bob = await _mk_user(db, "b2@h.io")
    team = await _mk_team_with_members(db, owner, [bob])
    group = await svc.get_or_create_team_group(db, team, owner.id)

    # human↔human (no agent @) → save only, but must broadcast + notify peer
    user_msg, agent_msg = await svc.dispatch_group(
        db, group, "你好大家", [f"user:{bob.id}"], owner_id=owner.id,
    )
    assert agent_msg is None
    assert user_msg.role == "user"

    # `message` event on the conversation stream
    conv_events = await redis_core.read_events(str(group.id), "0-0", block_ms=200)
    assert any('"type": "message"' in data for _id, data in conv_events)

    # `notify` to bob's personal stream with mention=true
    user_events = await redis_core.read_user_events(str(bob.id), "0-0", block_ms=200)
    assert any('"type": "notify"' in data and '"mention": true' in data for _id, data in user_events)


@pytest.mark.asyncio
async def test_mark_read_and_unread_summary(db):
    owner = await _mk_user(db, "o3@h.io")
    bob = await _mk_user(db, "b3@h.io")
    team = await _mk_team_with_members(db, owner, [bob])
    group = await svc.get_or_create_team_group(db, team, owner.id)

    # owner posts two messages mentioning bob
    await svc.dispatch_group(db, group, "msg1", [f"user:{bob.id}"], owner_id=owner.id)
    await svc.dispatch_group(db, group, "msg2", [], owner_id=owner.id)

    summary = await svc.unread_summary(db, bob.id, [group.id])
    assert summary[str(group.id)]["unread"] == 2
    assert summary[str(group.id)]["mention"] is True

    # owner's own messages are not unread for owner
    own = await svc.unread_summary(db, owner.id, [group.id])
    assert own[str(group.id)]["unread"] == 0

    # after marking read, bob has nothing unread
    await svc.mark_read(db, group.id, bob.id)
    summary2 = await svc.unread_summary(db, bob.id, [group.id])
    assert summary2[str(group.id)]["unread"] == 0
    assert summary2[str(group.id)]["mention"] is False


@pytest.mark.asyncio
async def test_team_group_is_idempotent_and_synced(db):
    owner = await _mk_user(db, "o4@h.io")
    bob = await _mk_user(db, "b4@h.io")
    team = await _mk_team_with_members(db, owner, [bob])

    g1 = await svc.get_or_create_team_group(db, team, owner.id)
    g2 = await svc.get_or_create_team_group(db, team, owner.id)
    assert g1.id == g2.id  # one canonical group per team

    members = await svc.get_group_members(db, g1.id)
    human_ids = {m.user_id for m in members if m.user_id}
    agent_ids = {m.agent_id for m in members if m.agent_id}
    assert owner.id in human_ids and bob.id in human_ids
    assert "hermes" in agent_ids

    # a new member joins the team → sync picks them up
    carol = await _mk_user(db, "c4@h.io")
    db.add(TeamMember(team_id=team.id, user_id=carol.id, role="member"))
    await db.flush()
    await svc.get_or_create_team_group(db, team, owner.id)
    members2 = await svc.get_group_members(db, g1.id)
    assert carol.id in {m.user_id for m in members2 if m.user_id}


@pytest.mark.asyncio
async def test_single_agent_mention_attribution(db):
    owner = await _mk_user(db, "o6@h.io")
    team = await _mk_team_with_members(db, owner, [])
    group = await svc.get_or_create_team_group(db, team, owner.id)
    # add a second agent so we can @-target a non-primary one
    await svc.add_group_member(db, group.id, agent_id="coder")

    user_msg, agent_msg = await svc.dispatch_group(
        db, group, "@coder 帮我写代码", ["coder"], owner_id=owner.id,
    )
    assert agent_msg is not None
    # reply attributed to the @-mentioned agent, primary_agent_id unchanged
    assert agent_msg.agent_id == "coder"
    assert group.primary_agent_id == "hermes"


@pytest.mark.asyncio
async def test_roundtable_keeps_distinct_profiles_sharing_agent_id(db):
    """Regression test: two Profiles wrapping the same underlying agent_id
    (e.g. two personas both built on "claude") must NOT collapse into a
    single roundtable target — each keeps its own identity/persona instead of
    the group silently falling back to one arbitrary "default" assistant.
    """
    import json as _json

    from app.config import settings
    from app.db.models.agent import Profile

    owner = await _mk_user(db, "o7@h.io")
    team = await _mk_team_with_members(db, owner, [])

    p1 = Profile(
        id=uuid.uuid4(), name="客服助手", handle="p1-support",
        default_agent_id="claude", system_prompt="你是客服助手", is_active=True,
    )
    p2 = Profile(
        id=uuid.uuid4(), name="代码助手", handle="p2-coder",
        default_agent_id="claude", system_prompt="你是代码助手", is_active=True,
    )
    db.add_all([p1, p2])
    await db.flush()
    team.shared_profile_ids = [str(p1.id), str(p2.id)]
    await db.flush()

    group = await svc.get_or_create_team_group(db, team, owner.id)
    members = await svc.get_group_members(db, group.id)
    ai_members = [m for m in members if m.agent_id]
    assert len(ai_members) == 2  # not collapsed to 1 despite sharing agent_id
    assert {m.profile_id for m in ai_members} == {p1.id, p2.id}

    # __all_agents__ must resolve to both distinct profiles, not a deduped
    # single "claude" bucket.
    resolved = await svc.resolve_mentions(db, group.id, ["__all_agents__"])
    assert len(resolved.agent_targets) == 2
    assert {pid for pid, _aid in resolved.agent_targets} == {str(p1.id), str(p2.id)}

    _, rt_msg = await svc.dispatch_group(
        db, group, "大家怎么看？", ["__all_agents__"], owner_id=owner.id,
    )
    assert rt_msg.role == "roundtable"
    replies = rt_msg.content["replies"]
    assert len(replies) == 2  # real roundtable, not a single collapsed reply
    assert {r["profile_id"] for r in replies} == {str(p1.id), str(p2.id)}

    # The enqueued runner task carries each profile's own system_prompt/persona
    # instead of one shared prompt applied to both participants.
    entries = await redis_core.get_redis().xrange(settings.acp_stream, "-", "+")
    task = _json.loads(entries[-1][1]["data"])
    assert task["type"] == "roundtable"
    prompts = {t["system_prompt"] for t in task["targets"]}
    assert prompts == {"你是客服助手", "你是代码助手"}


@pytest.mark.asyncio
async def test_edit_recall_reaction(db):
    owner = await _mk_user(db, "o5@h.io")
    team = await _mk_team_with_members(db, owner, [])
    group = await svc.get_or_create_team_group(db, team, owner.id)
    msg, _ = await svc.dispatch_group(db, group, "原文", [], owner_id=owner.id)

    edited = await svc.edit_message(db, msg, "改后")
    assert edited.content["text"] == "改后"
    assert edited.edited_at is not None

    react = await svc.toggle_reaction(db, msg, owner.id, "👍")
    assert react.reactions.get("👍") == [str(owner.id)]
    # toggling again removes it
    react2 = await svc.toggle_reaction(db, msg, owner.id, "👍")
    assert "👍" not in react2.reactions

    recalled = await svc.recall_message(db, msg)
    assert recalled.deleted_at is not None
    assert recalled.content["text"] == ""

    # message_update events were published for each change
    events = await redis_core.read_events(str(group.id), "0-0", block_ms=200)
    assert sum(1 for _id, data in events if '"type": "message_update"' in data) >= 3


@pytest.mark.asyncio
async def test_mention_targets_own_profile_not_conversation_default(db):
    """Regression: a group's Conversation.profile_id (the personal-chat-style
    "default assistant") must never override which Profile answers a single
    @-mention — only the mentioned GroupMember's own bound Profile may. This
    used to leak in via a profile_id_override parameter that outranked the
    resolved mention target.
    """
    from app.db.models.agent import Profile

    owner = await _mk_user(db, "o8@h.io")
    team = await _mk_team_with_members(db, owner, [])

    default_p = Profile(
        id=uuid.uuid4(), name="默认助手", handle="p-default",
        default_agent_id="hermes", system_prompt="我是默认助手", is_active=True,
    )
    coder_p = Profile(
        id=uuid.uuid4(), name="代码助手", handle="p-coder",
        default_agent_id="coder", system_prompt="我是代码助手", is_active=True,
    )
    db.add_all([default_p, coder_p])
    await db.flush()
    team.shared_profile_ids = [str(default_p.id)]
    await db.flush()

    group = await svc.get_or_create_team_group(db, team, owner.id)
    # Simulate a stray conversation-level "default profile" sitting on the
    # group — the historical leak vector.
    group.profile_id = str(default_p.id)
    await db.flush()

    await svc.add_group_member(db, group.id, agent_id="coder")
    members = await svc.get_group_members(db, group.id)
    coder_member = next(m for m in members if m.agent_id == "coder")
    coder_member.profile_id = coder_p.id
    await db.flush()

    _, agent_msg = await svc.dispatch_group(
        db, group, "@代码助手 帮我看看这段代码", [f"profile:{coder_p.id}"], owner_id=owner.id,
    )
    assert agent_msg is not None
    assert agent_msg.profile_id == coder_p.id
    assert agent_msg.agent_id == "coder"


@pytest.mark.asyncio
async def test_auto_reply_member_participates_without_mention(db):
    """A GroupMember with auto_reply=True answers even when nobody @-mentioned
    it, purely under channel_mode="mention" — no group-wide "always" needed.
    A member that neither opted into auto_reply nor was mentioned must not
    be targeted at all.
    """
    owner = await _mk_user(db, "o9@h.io")
    team = await _mk_team_with_members(db, owner, [])
    group = await svc.get_or_create_team_group(db, team, owner.id)  # channel_mode="mention"

    await svc.add_group_member(db, group.id, agent_id="coder")
    await svc.add_group_member(db, group.id, agent_id="writer")
    members = await svc.get_group_members(db, group.id)
    coder_member = next(m for m in members if m.agent_id == "coder")
    coder_member.auto_reply = True
    await db.flush()

    _, reply = await svc.dispatch_group(db, group, "大家好", [], owner_id=owner.id)
    assert reply is not None
    assert reply.role == "agent"  # single target => single-agent branch, not roundtable
    assert reply.agent_id == "coder"


@pytest.mark.asyncio
async def test_group_single_mention_injects_request_knowledge(db):
    """Message-level "reference this knowledge item" picker must actually
    reach the model's system_prompt in group chat, not just get stored as
    display-only content.knowledge_refs metadata.
    """
    import json as _json

    from app.config import settings
    from app.db.models.team import TeamKnowledge

    owner = await _mk_user(db, "o10@h.io")
    team = await _mk_team_with_members(db, owner, [])
    group = await svc.get_or_create_team_group(db, team, owner.id)
    await svc.add_group_member(db, group.id, agent_id="coder")

    k = TeamKnowledge(
        team_id=team.id, name="设计文档.md", kind="md",
        content="关键决策：使用 PostgreSQL", size_bytes=10,
    )
    db.add(k)
    await db.flush()

    await svc.dispatch_group(
        db, group, "@coder 参考一下", ["coder"], owner_id=owner.id,
        knowledge_ids=[str(k.id)],
    )

    entries = await redis_core.get_redis().xrange(settings.acp_stream, "-", "+")
    task = _json.loads(entries[-1][1]["data"])
    assert task["type"] == "single"
    assert "关键决策：使用 PostgreSQL" in (task["system_prompt"] or "")


@pytest.mark.asyncio
async def test_group_roundtable_injects_request_knowledge(db):
    """Same as above but for the roundtable (multi-target) branch."""
    import json as _json

    from app.config import settings
    from app.db.models.team import TeamKnowledge

    owner = await _mk_user(db, "o11@h.io")
    team = await _mk_team_with_members(db, owner, [])
    group = await svc.get_or_create_team_group(db, team, owner.id)
    await svc.add_group_member(db, group.id, agent_id="coder")
    await svc.add_group_member(db, group.id, agent_id="writer")

    k = TeamKnowledge(
        team_id=team.id, name="设计文档.md", kind="md",
        content="关键决策：使用 PostgreSQL", size_bytes=10,
    )
    db.add(k)
    await db.flush()

    await svc.dispatch_group(
        db, group, "大家怎么看？", ["__all_agents__"], owner_id=owner.id,
        knowledge_ids=[str(k.id)],
    )

    entries = await redis_core.get_redis().xrange(settings.acp_stream, "-", "+")
    task = _json.loads(entries[-1][1]["data"])
    assert task["type"] == "roundtable"
    assert all(
        "关键决策：使用 PostgreSQL" in (t["system_prompt"] or "") for t in task["targets"]
    )


@pytest.mark.asyncio
async def test_group_roundtable_attachment_gets_content_blocks(db):
    """Regression: an attachment in a roundtable/group turn must reach the
    runner as structured ACP content blocks (resource_link), not just a
    plain-text filename mention — this is what lets a roundtable agent
    actually read the file via read_file, and is required for images (which
    have no other representation in group chat) to reach the model at all.
    """
    import json as _json

    from app.config import settings
    from app.db.models.workspace import WorkspaceFile

    owner = await _mk_user(db, "o12@h.io")
    team = await _mk_team_with_members(db, owner, [])
    group = await svc.get_or_create_team_group(db, team, owner.id)
    await svc.add_group_member(db, group.id, agent_id="coder")
    await svc.add_group_member(db, group.id, agent_id="writer")

    wf = WorkspaceFile(
        conversation_id=group.id, name="notes.md", kind="md",
        content="# 项目笔记", size_bytes=10,
    )
    db.add(wf)
    await db.flush()

    await svc.dispatch_group(
        db, group, "大家看看这份笔记", ["__all_agents__"], owner_id=owner.id,
        attached_file_ids=[str(wf.id)],
    )

    entries = await redis_core.get_redis().xrange(settings.acp_stream, "-", "+")
    task = _json.loads(entries[-1][1]["data"])
    assert task["type"] == "roundtable"
    blocks = task.get("content_blocks") or []
    assert any(b.get("type") == "resource_link" and b.get("name") == "notes.md" for b in blocks)
