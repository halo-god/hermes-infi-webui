"""Multi-profile support: memory fallback, profile clone isolation,
skill team dimension, ZIP resource projection homes."""
import os
import uuid

import pytest

from app.config import settings
from app.db.models.agent import Profile
from app.db.models.team import Team, TeamMember
from app.db.models.user import User
from app.services import memory_service


async def _mk_user(db, email: str) -> User:
    from app.core.security import hash_password
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_profile(db, handle: str, owner: User, path: str | None = None) -> Profile:
    p = Profile(
        id=uuid.uuid4(), name=handle, handle=handle, default_agent_id="hermes",
        scope="personal", path=path,
    )
    db.add(p)
    await db.flush()
    return p


# ── memory fallback (profile-scoped wins, else global) ─────────────────────


@pytest.mark.asyncio
async def test_memory_profile_fallback(db):
    owner = await _mk_user(db, "mpm1@h.io")
    profile = await _mk_profile(db, "alpha", owner)

    # Global memory only -> profile read falls back to it.
    await memory_service.upsert_memory(db, owner.id, notes="全局备忘")
    mem = await memory_service.get_memory(db, owner.id, profile_id=profile.id)
    assert mem is not None and mem.notes == "全局备忘"

    # Profile-scoped memory wins over global.
    await memory_service.upsert_memory(db, owner.id, notes="专属备忘", profile_id=profile.id)
    mem = await memory_service.get_memory(db, owner.id, profile_id=profile.id)
    assert mem.notes == "专属备忘"
    # Global read still returns the global row.
    mem_global = await memory_service.get_memory(db, owner.id)
    assert mem_global.notes == "全局备忘"


@pytest.mark.asyncio
async def test_memory_episode_profile_scoped(db):
    from app.db.models.memory import MemoryEpisode
    from sqlalchemy import select
    owner = await _mk_user(db, "mpm2@h.io")
    profile = await _mk_profile(db, "beta", owner)
    from datetime import datetime, timezone
    await memory_service.add_episode(
        db, owner.id, None, "专属总结", "beta 会话总结", 10,
        datetime.now(timezone.utc), profile_id=profile.id,
    )
    rows = (await db.execute(
        select(MemoryEpisode).where(MemoryEpisode.profile_id == profile.id)
    )).scalars().all()
    assert len(rows) == 1 and rows[0].summary == "beta 会话总结"


# ── clone creates an isolated home (P1) ────────────────────────────────────


@pytest.mark.asyncio
async def test_clone_profile_gets_fresh_home(client, admin_headers, db, tmp_path, monkeypatch):
    """Cloning must produce a NEW HERMES_HOME (not share the source's) and
    carry the full per-profile bindings."""
    monkeypatch.setattr(settings, "hermes_home", str(tmp_path))
    monkeypatch.setattr("app.services.skill_sync_service._get_hermes_home", lambda: str(tmp_path))

    source_path = str(tmp_path / "profiles" / "source" / "config.yaml")
    os.makedirs(os.path.dirname(source_path), exist_ok=True)
    with open(source_path, "w", encoding="utf-8") as f:
        f.write("")

    p = Profile(
        id=uuid.uuid4(), name="源助手", handle="source", default_agent_id="hermes",
        scope="personal", path=source_path, system_prompt="我是源助手",
        knowledge_ids=["k1"], mcp_server_names=["mcp-x"], is_moa=True,
        moa_target_profile_ids=["t1"], is_chain=True, chain_target_profile_ids=["c1"],
        is_research=True, max_iterations=99, staged_enabled=True,
        staged_prompts={"clarify": "x"},
    )
    db.add(p)
    await db.commit()

    r = await client.post(f"/api/v1/profiles/{p.id}/clone", headers=admin_headers)
    assert r.status_code == 201, r.text
    clone = r.json()
    assert clone["handle"] != "source"
    assert clone["path"] and clone["path"] != source_path, "clone must not share the source home"
    assert os.path.isfile(clone["path"]), "clone home must exist on disk"
    # Full bindings carried over.
    fresh = (await db.execute(
        __import__("sqlalchemy").select(Profile).where(Profile.id == uuid.UUID(clone["id"]))
    )).scalar_one()
    assert fresh.knowledge_ids == ["k1"]
    assert fresh.mcp_server_names == ["mcp-x"]
    assert fresh.is_moa and fresh.moa_target_profile_ids == ["t1"]
    assert fresh.is_chain and fresh.chain_target_profile_ids == ["c1"]
    assert fresh.is_research
    assert fresh.max_iterations == 99
    assert fresh.staged_enabled and fresh.staged_prompts == {"clarify": "x"}


# ── skill team dimension (P4) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_skills_team_dimension_uses_user_teams(db):
    """A personal-scope profile (team_id=None) must still trigger skills from
    every team the user belongs to."""
    owner = await _mk_user(db, "mpt1@h.io")
    team = Team(id=uuid.uuid4(), name="技术团队", channel_mode="mention")
    db.add(team)
    await db.flush()
    db.add(TeamMember(team_id=team.id, user_id=owner.id, role="member"))
    await db.flush()

    skill = await memory_service.create_skill(
        db, name="团队技能", description="数据库调优",
        content="调优建议", trigger_conditions={"keywords": ["调优"]},
        team_id=team.id,
    )
    # profile has NO team_id — but the user is a team member.
    hits = await memory_service.search_skills(
        db, profile_id=None, owner_id=owner.id, team_id=None, query="帮我调优",
    )
    assert any(s.id == skill.id for s in hits), "team skill must trigger for a member's personal profile"


@pytest.mark.asyncio
async def test_search_skills_profile_ranked_first(db):
    """Profile-bound skills keep an injection slot over global always-on ones."""
    owner = await _mk_user(db, "mpt2@h.io")
    profile = await _mk_profile(db, "gamma", owner)
    profile_skill = await memory_service.create_skill(
        db, name="专属技能", description="专属描述",
        content="专属", trigger_conditions={"always": True},
        owner_id=owner.id, profile_id=profile.id,
    )
    await memory_service.create_skill(
        db, name="全局技能", description="全局描述",
        content="全局", trigger_conditions={"always": True},
        owner_id=owner.id,
    )
    hits = await memory_service.search_skills(
        db, profile_id=profile.id, owner_id=owner.id, team_id=None, query="任意消息",
        limit=2,
    )
    assert hits and hits[0].id == profile_skill.id, "profile skill must rank first"
    assert len(hits) == 2
