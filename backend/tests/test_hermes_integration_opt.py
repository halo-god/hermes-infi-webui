"""Platform-side hermes-agent integration optimizations.

Covers the batches shipped for basic-functionality hardening:

- Batch 1 (skill data safety): slug collisions (CJK / similar names) no
  longer overwrite each other; frontmatter keeps the original name; rename
  and delete clean up stale dirs via the platform_skill_id marker; Direction
  B matches by id and tombstones agent-created skills whose FS dir vanished.
- Batch 3 (config): reasoning_effort lands under agent: (hermes reads it
  only there) and the legacy top-level key is stripped; profile config sync
  skips profiles deleted from the DB.
"""
import os
import uuid
from unittest import mock

import pytest

from app.config import settings
from app.db.models.memory import AgentSkill
from app.services import memory_service, skill_sync_service as sss


def _mk_skill_dir(home, slug, *, name=None, skill_id=None, content="body"):
    d = os.path.join(home, "skills", slug)
    os.makedirs(d, exist_ok=True)
    meta = {"name": name or slug, "description": "desc"}
    if skill_id:
        meta["metadata"] = {"platform_skill_id": skill_id}
    import yaml
    front = yaml.dump(meta, allow_unicode=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\n{front}---\n\n{content}\n")
    return d


# ── Batch 1: slug collision & id marker ─────────────────────────────────────


def test_slugify_cjk_and_collisions():
    assert sss._slugify("文件处理") == "unnamed-skill"
    assert sss._slugify("API 测试") == "api"
    assert sss._slugify("API 开发") == "api"  # same slug — must not overwrite


def test_render_skill_md_keeps_original_name_and_id(tmp_path):
    md = sss._render_skill_md(
        "文件处理", "desc", "内容", tags=["a"], skill_id=str(uuid.uuid4()),
    )
    assert "name: 文件处理" in md  # original CJK name, not "unnamed-skill"
    assert "platform_skill_id" in md
    assert md.count("unnamed-skill") == 0


def test_colliding_slugs_get_hash_suffix(tmp_path, monkeypatch):
    """Two skills whose slugs both degrade to 'api' must live in separate dirs."""
    monkeypatch.setattr(settings, "hermes_skills_sync_enabled", True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(home))

    id_a = uuid.uuid4()
    id_b = uuid.uuid4()
    sss._write_skill_to_home(
        str(home), "api", sss._render_skill_md("API 测试", "d", "内容A", skill_id=str(id_a)),
        enabled=True, name="API 测试", skill_id=str(id_a),
    )
    sss._write_skill_to_home(
        str(home), "api", sss._render_skill_md("API 开发", "d", "内容B", skill_id=str(id_b)),
        enabled=True, name="API 开发", skill_id=str(id_b),
    )

    dirs = sorted(os.listdir(home / "skills"))
    assert len(dirs) == 2, f"expected 2 distinct dirs, got {dirs}"
    assert any("api-" in d for d in dirs)
    # First skill's dir still intact (not overwritten).
    first = home / "skills" / "api"
    assert first.exists() and "内容A" in (first / "SKILL.md").read_text(encoding="utf-8")


def test_rename_removes_old_dir_via_id(tmp_path, monkeypatch):
    """update_skill's remove(old) + sync(new) must leave no stale dirs."""
    monkeypatch.setattr(settings, "hermes_skills_sync_enabled", True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(home))
    monkeypatch.setattr(sss, "_skill_profile_id", mock.AsyncMock(return_value=None))

    sid = uuid.uuid4()
    sss._write_skill_to_home(
        str(home), "oldname",
        sss._render_skill_md("OldName", "d", "旧", skill_id=str(sid)),
        enabled=True, name="OldName", skill_id=str(sid),
    )
    # Rename: remove old (by id marker) then write new slug.
    sss._remove_skill_dir_by_id(str(home), str(sid))
    sss._write_skill_to_home(
        str(home), "newname",
        sss._render_skill_md("NewName", "d", "新", skill_id=str(sid)),
        enabled=True, name="NewName", skill_id=str(sid),
    )
    assert not (home / "skills" / "oldname").exists(), "old dir must be removed on rename"
    assert (home / "skills" / "newname").exists()


def test_remove_skill_by_id_catches_hash_dirs(tmp_path, monkeypatch):
    """delete_skill must remove hash-suffixed collision dirs, not just the slug."""
    monkeypatch.setattr(settings, "hermes_skills_sync_enabled", True)
    home = tmp_path / "home"
    home.mkdir()
    sid = uuid.uuid4()
    _mk_skill_dir(str(home), "api", name="API 测试", skill_id=str(sid))
    _mk_skill_dir(str(home), "api-abcdef12", name="API 开发", skill_id=str(sid))
    _mk_skill_dir(str(home), "other", name="其他技能")

    sss._remove_skill_dir_by_id(str(home), str(sid))
    remaining = sorted(os.listdir(home / "skills"))
    assert remaining == ["other"], f"id-marked dirs must all be removed, got {remaining}"


def test_remove_by_name_never_touches_unrelated_slug_collision_dir(tmp_path):
    """Renaming a CJK skill must NOT delete an unrelated legacy dir whose slug
    collides (both degrade to 'unnamed-skill') — that dir holds OTHER content."""
    home = tmp_path / "home"
    (home / "skills").mkdir(parents=True)
    # Unrelated legacy skill in the bare slug dir (no id marker, different name).
    _mk_skill_dir(str(home), "unnamed-skill", name="技能甲", content="甲的独有内容")
    # The skill being renamed lives in its own hash dir.
    sid = uuid.uuid4()
    _mk_skill_dir(str(home), f"unnamed-skill-{sid.hex[:8]}", name="文件处理", skill_id=str(sid))

    sss._remove_skill_dir_by_name(str(home), "文件处理")

    # 技能甲's dir must survive; only 文件处理's legacy match is removed.
    assert (home / "skills" / "unnamed-skill" / "SKILL.md").exists(), \
        "unrelated slug-collision dir must not be deleted"
    assert "甲的独有内容" in (home / "skills" / "unnamed-skill" / "SKILL.md").read_text(encoding="utf-8")


# ── Batch 1: Direction B matching & tombstone ───────────────────────────────


@pytest.mark.asyncio
async def test_ingest_matches_by_platform_id_not_slug(db, tmp_path, monkeypatch):
    """A platform-written CJK skill (slug degraded) must match its own DB row."""
    from app.db.models.user import User
    from app.core.security import hash_password

    owner = User(
        id=uuid.uuid4(), email="ing1@h.io", name="ing1",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(owner)
    await db.flush()

    global_home = tmp_path / "home"
    global_home.mkdir()
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(global_home))

    # Platform skill written to FS with id marker (slug degraded to unnamed).
    sid = uuid.uuid4()
    _mk_skill_dir(str(global_home), "unnamed-skill", name="文件处理", skill_id=str(sid))
    skill = AgentSkill(
        id=sid, owner_id=owner.id, name="文件处理", description="d",
        content="平台内容", trigger_conditions={}, enabled=True, origin="platform",
    )
    db.add(skill)
    await db.flush()

    result = await sss.ingest_hermes_skills(db, owner.id)
    assert result["new"] == 0, "id-marked skill must match its existing DB row"
    assert result["tombstoned"] == 0


@pytest.mark.asyncio
async def test_ingest_tombstones_agent_skill_but_not_platform(db, tmp_path, monkeypatch):
    """FS-deleted agent skills get disabled; platform skills stay untouched."""
    from app.db.models.user import User
    from app.core.security import hash_password

    owner = User(
        id=uuid.uuid4(), email="ing2@h.io", name="ing2",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(owner)
    await db.flush()

    global_home = tmp_path / "home"
    global_home.mkdir()
    monkeypatch.setattr(sss, "_get_hermes_home", lambda: str(global_home))

    # Agent-created skill: DB row exists, but its FS dir is gone.
    agent_skill = AgentSkill(
        id=uuid.uuid4(), owner_id=owner.id, name="已删除技能", description="d",
        content="c", trigger_conditions={}, enabled=True, origin="agent",
    )
    # Platform skill: DB row, no FS dir, must NOT be tombstoned.
    plat_skill = AgentSkill(
        id=uuid.uuid4(), owner_id=owner.id, name="平台技能", description="d",
        content="c", trigger_conditions={}, enabled=True, origin="platform",
    )
    db.add_all([agent_skill, plat_skill])
    await db.flush()

    result = await sss.ingest_hermes_skills(db, owner.id)
    assert result["tombstoned"] == 1
    await db.refresh(agent_skill)
    await db.refresh(plat_skill)
    assert agent_skill.enabled is False, "agent skill vanished from FS must be tombstoned"
    assert plat_skill.enabled is True, "platform skill is DB truth — never tombstoned"


# ── Batch 3: reasoning_effort config level ──────────────────────────────────


def test_config_overrides_reasoning_effort_under_agent(monkeypatch):
    from app.services import hermes_config_sync as hcs
    monkeypatch.setattr(settings, "hermes_reasoning_effort", "high")
    overrides = hcs._build_config_overrides()
    assert overrides["agent"]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in overrides, "top-level key must not be written"


def test_sync_config_strips_legacy_top_level_reasoning_effort(tmp_path):
    from app.services import hermes_config_sync as hcs
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "reasoning_effort: medium\nagent:\n  model: deepseek\n",
        encoding="utf-8",
    )
    hcs._sync_config_file(str(cfg), hcs._build_config_overrides())
    import yaml
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert "reasoning_effort" not in data, "legacy top-level key must be stripped"
    assert data["agent"]["reasoning_effort"] is not None


# ── Batch 4: consolidation plan building ────────────────────────────────────


def test_build_plan_sections_respects_budget():
    from agent_runner.runner_memory import _build_plan_sections
    from app.db.models.conversation import Conversation, Message

    c1 = Conversation(id=uuid.uuid4(), title="会话一", owner_id=uuid.uuid4())
    c2 = Conversation(id=uuid.uuid4(), title="会话二", owner_id=uuid.uuid4())
    msgs = {
        c1.id: [Message(conversation_id=c1.id, role="user", content={"text": "你好"})],
        c2.id: [Message(conversation_id=c2.id, role="agent", content={"text": "回复内容"})],
    }
    sections, meta = _build_plan_sections([c1, c2], msgs, budget=5000)
    assert len(sections) == 2
    assert "会话一" in sections[0] and "你好" in sections[0]
    assert meta[0][0] == c1.id and meta[1][0] == c2.id
    total = sum(len(s) for s in sections)
    assert total <= 5000


@pytest.mark.asyncio
async def test_memory_upsert_profile_scoped_does_not_touch_global(db):
    """Batch 4: profile consolidation writes its own row, never the global one."""
    from app.db.models.agent import Profile
    from app.db.models.user import User
    from app.core.security import hash_password

    owner = User(
        id=uuid.uuid4(), email="mem4@h.io", name="mem4",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(owner)
    await db.flush()
    profile = Profile(
        id=uuid.uuid4(), name="alpha", handle="alpha", default_agent_id="hermes",
        scope="personal",
    )
    db.add(profile)
    await db.flush()

    await memory_service.upsert_memory(db, owner.id, notes="全局备忘")
    await memory_service.upsert_memory(
        db, owner.id, notes="专属备忘", profile_id=profile.id,
    )
    # The profile write must not have mutated the global row.
    global_mem = await memory_service.get_memory(db, owner.id)
    profile_mem = await memory_service.get_memory(db, owner.id, profile_id=profile.id)
    assert global_mem.notes == "全局备忘"
    assert profile_mem.notes == "专属备忘"


# ── Batch 2: profile handle rename migrates FS home ─────────────────────────


@pytest.mark.asyncio
async def test_update_profile_handle_migrates_home(client, admin_headers, db, tmp_path, monkeypatch):
    """Renaming a profile's handle must move profiles/<old> → profiles/<new>."""
    from app.api.v1.agents import _ensure_profile_home
    from app.db.models.agent import Profile

    hermes_root = tmp_path / "hermes"
    hermes_root.mkdir()
    monkeypatch.setattr(settings, "hermes_home", str(hermes_root))
    home = _ensure_profile_home("old-handle")
    assert home and os.path.isdir(os.path.join(hermes_root, "profiles", "old-handle"))

    p = Profile(
        id=uuid.uuid4(), name="旧名", handle="old-handle", default_agent_id="hermes",
        scope="personal", path=home,
    )
    db.add(p)
    await db.commit()

    resp = await client.patch(
        f"/api/v1/profiles/{p.id}",
        json={"handle": "new-handle"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    new_home = os.path.join(hermes_root, "profiles", "new-handle")
    assert os.path.isdir(new_home), "FS home must be migrated on handle rename"
    assert not os.path.exists(os.path.join(hermes_root, "profiles", "old-handle")), \
        "old home must not linger as an orphan"

    await db.refresh(p)
    assert p.handle == "new-handle"
    assert p.path == os.path.join(new_home, "config.yaml")
