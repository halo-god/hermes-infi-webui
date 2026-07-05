"""Stage D1: skill_proposals schema + trigger/status queue skeleton.

optimizer.run_evolution() is a stub (see optimizer.py's module docstring) —
these tests validate the gate logic and the queue/proposal plumbing, not
any actual quality of the generated content.
"""
from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core import redis as redis_core
from app.core.security import create_token, hash_password
from app.db.base import async_session_maker
from app.db.models.skill_evolution import SkillProposal
from app.db.models.user import User
from app.services import memory_service
from skill_evolution.optimizer import EvolutionGateFailure, run_evolution


async def _mk_user(db, email: str, role: str = "member") -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role=role,
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_skill(db, owner: User, **kwargs):
    defaults = dict(
        name="限流技能", description="限流相关问题",
        content="回答限流问题时提醒检查 rl:msg:{user} 键",
        trigger_conditions={"keywords": ["限流"]}, owner_id=owner.id,
    )
    defaults.update(kwargs)
    return await memory_service.create_skill(db, **defaults)


# ── optimizer.run_evolution() ───────────────────────────────────────────────

async def test_run_evolution_stub_produces_gated_candidate(db):
    owner = await _mk_user(db, "opt-owner@h.io")
    skill = await _mk_skill(db, owner)

    result = await run_evolution(db, skill)

    assert result.eval_score_after > result.eval_score_before
    assert result.proposed_content != skill.content
    assert result.proposed_content.startswith(skill.content)
    assert 0 <= result.diff_ratio <= 1
    assert result.dataset_summary["real_count"] == 0


async def test_run_evolution_rejects_when_score_gate_too_strict(db, monkeypatch):
    from app.config import settings
    owner = await _mk_user(db, "opt-owner2@h.io")
    skill = await _mk_skill(db, owner)

    monkeypatch.setattr(settings, "skill_evolution_min_score_improvement", 999.0)

    with pytest.raises(EvolutionGateFailure, match="分数提升不足"):
        await run_evolution(db, skill)


async def test_run_evolution_rejects_when_size_gate_too_strict(db, monkeypatch):
    from app.config import settings
    owner = await _mk_user(db, "opt-owner3@h.io")
    skill = await _mk_skill(db, owner)

    monkeypatch.setattr(settings, "skill_evolution_max_content_bytes", 1)

    with pytest.raises(EvolutionGateFailure, match="超出大小上限"):
        await run_evolution(db, skill)


async def test_run_evolution_rejects_when_diff_ratio_gate_too_strict(db, monkeypatch):
    from app.config import settings
    owner = await _mk_user(db, "opt-owner4@h.io")
    skill = await _mk_skill(db, owner)

    monkeypatch.setattr(settings, "skill_evolution_max_content_diff_ratio", 0.0)

    with pytest.raises(EvolutionGateFailure, match="改动幅度超出上限"):
        await run_evolution(db, skill)


# ── runner_skill_evolution.handle_skill_evolution() ─────────────────────────

@pytest_asyncio.fixture
async def real_db():
    """handle_skill_evolution opens its own async_session_maker() session, so
    data must be genuinely committed to be visible to it — see
    test_skill_firings.py's identical fixture for the underlying
    cross-connection visibility issue."""
    session = async_session_maker()
    owner_ids: list[uuid.UUID] = []
    try:
        yield session, owner_ids
    finally:
        for uid in owner_ids:
            await session.execute(delete(User).where(User.id == uid))
        await session.commit()
        await session.close()


async def _mk_user_committed(db, owner_ids, email: str) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    owner_ids.append(u.id)
    return u


async def _mk_skill_committed(db, owner: User, **kwargs):
    defaults = dict(
        name="限流技能", description="限流相关问题",
        content="回答限流问题时提醒检查 rl:msg:{user} 键",
        trigger_conditions={"keywords": ["限流"]}, owner_id=owner.id,
    )
    defaults.update(kwargs)
    return await memory_service.create_skill(db, **defaults)


async def test_handle_skill_evolution_creates_pending_proposal(real_db):
    from agent_runner.runner_skill_evolution import handle_skill_evolution

    db, owner_ids = real_db
    owner = await _mk_user_committed(db, owner_ids, "runner-owner@h.io")
    skill = await _mk_skill_committed(db, owner)

    await handle_skill_evolution({"skill_id": str(skill.id)}, agents={})

    rows = list((await db.execute(
        select(SkillProposal).where(SkillProposal.skill_id == skill.id)
    )).scalars().all())
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].proposed_content != skill.content

    r = redis_core.get_redis()
    raw = await r.get(redis_core.skill_evolution_status_key(str(skill.id)))
    assert json.loads(raw)["status"] == "done"


async def test_handle_skill_evolution_gate_failure_creates_no_proposal(real_db, monkeypatch):
    from app.config import settings
    from agent_runner.runner_skill_evolution import handle_skill_evolution

    db, owner_ids = real_db
    owner = await _mk_user_committed(db, owner_ids, "runner-owner2@h.io")
    skill = await _mk_skill_committed(db, owner, name="另一个技能")

    monkeypatch.setattr(settings, "skill_evolution_min_score_improvement", 999.0)

    await handle_skill_evolution({"skill_id": str(skill.id)}, agents={})

    rows = list((await db.execute(
        select(SkillProposal).where(SkillProposal.skill_id == skill.id)
    )).scalars().all())
    assert rows == []

    r = redis_core.get_redis()
    raw = await r.get(redis_core.skill_evolution_status_key(str(skill.id)))
    assert json.loads(raw)["status"] == "error"


async def test_handle_skill_evolution_missing_skill_sets_error_status():
    from agent_runner.runner_skill_evolution import handle_skill_evolution

    bogus_id = str(uuid.uuid4())
    await handle_skill_evolution({"skill_id": bogus_id}, agents={})

    r = redis_core.get_redis()
    raw = await r.get(redis_core.skill_evolution_status_key(bogus_id))
    data = json.loads(raw)
    assert data["status"] == "error"
    assert "不存在" in data["detail"]


# ── API: trigger + status endpoints ─────────────────────────────────────────

@pytest_asyncio.fixture
async def super_admin_user(db) -> User:
    user = User(
        id=uuid.uuid4(), email="root-d1@hermes.io", name="Root",
        password_hash=hash_password("Root@1234"), is_active=True, role="super_admin",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest.fixture
def super_admin_headers(super_admin_user: User) -> dict[str, str]:
    token, _ = create_token(str(super_admin_user.id), "access")
    return {"Authorization": f"Bearer {token}"}


async def test_trigger_evolve_endpoint_queues_and_locks(client, super_admin_headers, super_admin_user, db):
    skill = await _mk_skill(db, super_admin_user)
    await db.commit()

    resp = await client.post(
        f"/api/v1/skill-evolution/skills/{skill.id}/evolve", headers=super_admin_headers,
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"

    resp2 = await client.post(
        f"/api/v1/skill-evolution/skills/{skill.id}/evolve", headers=super_admin_headers,
    )
    assert resp2.status_code == 409


async def test_trigger_evolve_endpoint_404_for_unknown_skill(client, super_admin_headers):
    resp = await client.post(
        f"/api/v1/skill-evolution/skills/{uuid.uuid4()}/evolve", headers=super_admin_headers,
    )
    assert resp.status_code == 404


async def test_trigger_evolve_endpoint_forbidden_for_regular_user(client, auth_headers, test_user, db):
    skill = await _mk_skill(db, test_user)
    await db.commit()

    resp = await client.post(
        f"/api/v1/skill-evolution/skills/{skill.id}/evolve", headers=auth_headers,
    )
    assert resp.status_code == 403


async def test_evolve_status_endpoint_defaults_to_idle(client, super_admin_headers):
    resp = await client.get(
        f"/api/v1/skill-evolution/skills/{uuid.uuid4()}/evolve/status", headers=super_admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"
