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
    team = Team(id=uuid.uuid4(), name="Squad", shared_agents=["hermes"], channel_mode="mention")
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
