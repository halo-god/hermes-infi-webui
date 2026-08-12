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
