"""Multi-profile skill sync: evolution results must reach the RIGHT home.

Each profile runs its agent with HERMES_HOME=<home>/profiles/<handle> and
reads ONLY its own skills/ dir. So:

- a skill bound to a profile (AgentSkill.profile_id) is written to that
  profile's home only;
- a global / owner / team skill is projected to the global skills/ dir AND
  every profile home (otherwise profile agents can't use it);
- Direction B (scan) ingests per-profile home skills bound to that profile.
"""
import uuid
from unittest import mock

import pytest

from app.config import settings
from app.services import skill_sync_service as sss


@pytest.mark.asyncio
async def test_profile_bound_skill_writes_only_that_profile_home(tmp_path, monkeypatch):
    """A skill with profile_id lands in the profile's home — NOT the global dir."""
    monkeypatch.setattr(settings, "hermes_skills_sync_enabled", True)
    pid = uuid.uuid4()
    profile_home = tmp_path / "profiles" / "emotion-master"
    profile_home.mkdir(parents=True)
    global_home = tmp_path / "home"
    global_home.mkdir()

    monkeypatch.setattr(sss, "_skill_profile_id", mock.AsyncMock(return_value=pid))
    monkeypatch.setattr(sss, "_profile_home", mock.AsyncMock(return_value=str(profile_home)))
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(global_home))

    await sss.sync_skill_to_hermes(
        uuid.uuid4(), "TestSkill", "desc", "内容", True, {"keywords": ["测试"]},
    )

    profile_skill = profile_home / "skills" / "testskill" / "SKILL.md"
    assert profile_skill.exists(), "profile-bound skill must land in the profile home"
    assert "内容" in profile_skill.read_text(encoding="utf-8")
    assert not (global_home / "skills" / "testskill").exists(), \
        "profile-bound skill must NOT leak into the global dir"


@pytest.mark.asyncio
async def test_global_skill_projects_to_global_and_all_profile_homes(tmp_path, monkeypatch):
    """A global (owner/team) skill must be usable by every profile agent."""
    monkeypatch.setattr(settings, "hermes_skills_sync_enabled", True)
    global_home = tmp_path / "home"
    global_home.mkdir()
    alpha = global_home / "profiles" / "alpha"
    beta = global_home / "profiles" / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)

    monkeypatch.setattr(sss, "_skill_profile_id", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(global_home))

    await sss.sync_skill_to_hermes(
        uuid.uuid4(), "GlobalSkill", "desc", "全局技能", True, None,
    )

    assert (global_home / "skills" / "globalskill" / "SKILL.md").exists()
    assert (alpha / "skills" / "globalskill" / "SKILL.md").exists(), \
        "global skill must be projected into every profile home"
    assert (beta / "skills" / "globalskill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_disabled_skill_removed_from_all_homes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hermes_skills_sync_enabled", True)
    global_home = tmp_path / "home"
    global_home.mkdir()
    alpha = global_home / "profiles" / "alpha"
    alpha.mkdir(parents=True)
    for home in (global_home, alpha):
        d = home / "skills" / "oldskill"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("old", encoding="utf-8")

    monkeypatch.setattr(sss, "_skill_profile_id", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(global_home))

    await sss.sync_skill_to_hermes(
        uuid.uuid4(), "OldSkill", "desc", "内容", False, None,
    )
    assert not (global_home / "skills" / "oldskill").exists()
    assert not (alpha / "skills" / "oldskill").exists()


@pytest.mark.asyncio
async def test_ingest_binds_profile_home_skills_to_profile(db, tmp_path, monkeypatch):
    """Direction B: skills under profiles/<handle>/skills are ingested with
    profile_id set — closing the loop for per-profile agent-created skills."""
    from app.db.models.agent import Profile
    from app.db.models.memory import AgentSkill
    from app.db.models.user import User
    from app.core.security import hash_password
    from sqlalchemy import select

    owner = User(
        id=uuid.uuid4(), email="mp1@h.io", name="mp1",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(owner)
    await db.flush()

    profile_home = tmp_path / "profiles" / "alpha"
    profile_home.mkdir(parents=True)
    skill_dir = profile_home / "skills" / "perf-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: PerfSkill\ndescription: 性能优化\n---\n\n性能优化内容\n",
        encoding="utf-8",
    )
    profile = Profile(
        id=uuid.uuid4(), name="alpha", handle="alpha", default_agent_id="hermes",
        scope="personal",
        path=str(profile_home / "config.yaml"),
    )
    db.add(profile)
    await db.flush()

    result = await sss.ingest_hermes_skills(db, owner.id)
    assert result["new"] >= 1

    row = (await db.execute(
        select(AgentSkill).where(AgentSkill.name == "PerfSkill")
    )).scalars().first()
    assert row is not None
    assert row.profile_id == profile.id, "profile-home skill must bind to the profile"
    assert "性能优化内容" in row.content


@pytest.mark.asyncio
async def test_a_b_sync_loop_converges_via_content_hash(tmp_path, monkeypatch):
    """The A→B→A sync loop must converge instead of bouncing the DB.

    Direction A writes the frontmatter content_hash and records it on the row;
    Direction B then recognises the file as a projection of the DB and skips
    it — so a platform/evolution edit is NOT rolled back by the next scan,
    while a genuine external edit is still ingested.
    """
    from app.core.security import hash_password
    from app.db.base import async_session_maker
    from app.db.models.memory import AgentSkill
    from app.db.models.user import User
    from sqlalchemy import select

    monkeypatch.setattr(settings, "hermes_skills_sync_enabled", True)
    global_home = tmp_path / "home"
    global_home.mkdir()
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(global_home))
    monkeypatch.setattr(sss, "_skill_profile_id", mock.AsyncMock(return_value=None))

    async with async_session_maker() as s:
        owner = User(
            id=uuid.uuid4(), email="loop1@h.io", name="loop1",
            password_hash=hash_password("Test@1234"), is_active=True, role="member",
        )
        s.add(owner)
        await s.commit()
        skill = AgentSkill(
            owner_id=owner.id, name="LoopSkill", description="d",
            content="v1 内容", enabled=True, origin="platform",
        )
        s.add(skill)
        await s.commit()
        skill_id = skill.id

    # Direction A: project to FS (writes content_hash into frontmatter + row).
    await sss.sync_skill_to_hermes(
        skill_id, "LoopSkill", "d", "v1 内容", True, None,
    )
    path = global_home / "skills" / "loopskill" / "SKILL.md"
    assert path.exists()

    # Direction B: the file is a projection — DB must NOT be touched.
    # (ingest runs in its own session: the db fixture's outer transaction
    # would hold a row lock that deadlocks the next sync's UPDATE)
    async with async_session_maker() as s:
        res = await sss.ingest_hermes_skills(s, owner.id)
    assert res["new"] == 0 and res["updated"] == 0, res

    # External edit of a PLATFORM skill's projection → NOT ingested.
    # Platform rows are DB-owned (AGENTS.md invariant: "平台技能永不受扫描
    # 影响"); a drifted FS copy is ignored so platform edits can never be
    # silently rolled back by a scan.
    path.write_text(
        path.read_text(encoding="utf-8").replace("v1 内容", "v2 外部修改"),
        encoding="utf-8",
    )
    async with async_session_maker() as s:
        res = await sss.ingest_hermes_skills(s, owner.id)
    assert res["updated"] == 0, res
    async with async_session_maker() as s:
        row = (await s.execute(
            select(AgentSkill).where(AgentSkill.id == skill_id)
        )).scalar_one()
        assert row.content == "v1 内容"  # DB stays the source of truth

    # Platform edit (evolution approval): DB content updated first, then
    # projected to FS → next scan must converge (no bounce back to v2).
    async with async_session_maker() as s:
        row = (await s.execute(
            select(AgentSkill).where(AgentSkill.id == skill_id)
        )).scalar_one()
        row.content = "v3 演化内容"
        await s.commit()
    await sss.sync_skill_to_hermes(
        skill_id, "LoopSkill", "d", "v3 演化内容", True, None,
    )
    async with async_session_maker() as s:
        res = await sss.ingest_hermes_skills(s, owner.id)
    assert res["new"] == 0 and res["updated"] == 0, res

    # The DB row survived both scans with the evolution content — no bounce.
    async with async_session_maker() as s:
        row = (await s.execute(
            select(AgentSkill).where(AgentSkill.id == skill_id)
        )).scalar_one()
        assert row.content == "v3 演化内容"
        assert row.content_hash == sss._hash_content("v3 演化内容")


@pytest.mark.asyncio
async def test_agent_origin_skill_external_edit_is_ingested(tmp_path, monkeypatch):
    """FS wins for AGENT-origin rows: an external edit of a hermes-side skill
    file is absorbed back into the DB (only platform rows are scan-immune)."""
    from app.db.base import async_session_maker
    from app.db.models.agent import Profile  # noqa: F401 (schema graph)
    from app.db.models.memory import AgentSkill
    from app.db.models.user import User
    from app.core.security import hash_password
    from sqlalchemy import select

    monkeypatch.setattr(settings, "hermes_skills_sync_enabled", True)
    global_home = tmp_path / "home"
    global_home.mkdir()
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(global_home))
    monkeypatch.setattr(sss, "_skill_profile_id", mock.AsyncMock(return_value=None))

    async with async_session_maker() as s:
        owner = User(
            id=uuid.uuid4(), email="agented@h.io", name="agented",
            password_hash=hash_password("Test@1234"), is_active=True, role="member",
        )
        s.add(owner)
        await s.commit()
    # Simulate a hermes-side skill a previous scan ingested.
    async with async_session_maker() as s:
        skill = AgentSkill(
            owner_id=owner.id, name="HermesSide", description="d",
            content="hermes v1", enabled=True, origin="agent",
            content_hash=sss._hash_content("hermes v1"),
        )
        s.add(skill)
        await s.commit()
        skill_id = skill.id

    # The file drifted on the hermes side (real edit, not a projection).
    skill_dir = global_home / "skills" / "hermesside"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: HermesSide\ndescription: d\n---\nhermes v2 编辑", encoding="utf-8",
    )

    async with async_session_maker() as s:
        res = await sss.ingest_hermes_skills(s, owner.id)
    assert res["updated"] == 1, res
    assert res["new"] == 0, res

    async with async_session_maker() as s:
        row = (await s.execute(
            select(AgentSkill).where(AgentSkill.id == skill_id)
        )).scalar_one()
        assert "v2 编辑" in row.content
