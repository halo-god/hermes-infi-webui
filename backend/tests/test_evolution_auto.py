"""Auto-evolution ("Self-improvement review") — automatic trigger + auto-apply.

Guards the two auto paths added on top of the manual admin flows:

1. should_trigger_skill / should_trigger_profile decide whether a completed
   turn should enqueue an evolution run (auto switch + real optimizer on,
   sample threshold reached, cooldown elapsed).
2. The runner auto-applies a proposal that passed the gates (reviewed_by=None,
   review_note="auto-applied") — and ONLY when the real optimizer is on; the
   LLM-free stub's placeholder proposals must never auto-apply.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.db.models.profile_evolution import ProfileFiring
from app.db.models.skill_evolution import SkillFiring, SkillProposal
from app.services import evolution_service
from app.services import memory_service


async def _mk_user(db, email: str):
    from app.core.security import hash_password
    from app.db.models.user import User
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_skill(db, owner, name="自动演化技能"):
    return await memory_service.create_skill(
        db, name=name, description="自动演化测试",
        content="基线内容", trigger_conditions={"keywords": ["演化"]},
        owner_id=owner.id,
    )


async def _mk_firing_message(db, owner):
    """A real message row — SkillFiring/ProfileFiring.message_id has an FK."""
    from app.db.models.conversation import Conversation, Message
    convo = Conversation(owner_id=owner.id, title="演化样本", primary_agent_id="hermes")
    db.add(convo)
    await db.flush()
    m = Message(
        conversation_id=convo.id, owner_id=owner.id, role="user",
        content={"text": "样本"}, status="complete",
    )
    db.add(m)
    await db.flush()
    return m


def _enable_auto(monkeypatch, *, real_optimizer: bool = True):
    monkeypatch.setattr(settings, "evolution_auto_enabled", True)
    monkeypatch.setattr(settings, "skill_evolution_enabled", real_optimizer)
    monkeypatch.setattr(settings, "evolution_auto_min_firings", 2)
    monkeypatch.setattr(settings, "evolution_auto_cooldown_hours", 24)


# ── trigger decision (evolution_service) ────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_trigger_fires_when_samples_and_cooldown_met(db, monkeypatch):
    _enable_auto(monkeypatch)
    owner = await _mk_user(db, "auto1@h.io")
    skill = await _mk_skill(db, owner)
    for i in range(2):
        m = await _mk_firing_message(db, owner)
        db.add(SkillFiring(
            skill_id=skill.id, message_id=m.id,
            conversation_id=m.conversation_id, trigger_query_excerpt=f"q{i}",
        ))
    await db.flush()

    assert await evolution_service.should_trigger_skill(db, skill.id) is True


@pytest.mark.asyncio
async def test_auto_trigger_respects_cooldown(db, monkeypatch):
    _enable_auto(monkeypatch)
    owner = await _mk_user(db, "auto2@h.io")
    skill = await _mk_skill(db, owner)
    for i in range(2):
        m = await _mk_firing_message(db, owner)
        db.add(SkillFiring(
            skill_id=skill.id, message_id=m.id,
            conversation_id=m.conversation_id, trigger_query_excerpt=f"q{i}",
        ))
    # A recent proposal (inside the cooldown window) suppresses auto-trigger.
    db.add(SkillProposal(
        skill_id=skill.id, proposed_content="x", rationale="r",
        eval_score_before=0.5, eval_score_after=0.6, diff_ratio=0.1,
        dataset_summary={}, status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    await db.flush()

    assert await evolution_service.should_trigger_skill(db, skill.id) is False


@pytest.mark.asyncio
async def test_auto_trigger_skips_when_stub_optimizer(db, monkeypatch):
    """Auto mode must never run (or auto-apply) the LLM-free stub."""
    _enable_auto(monkeypatch, real_optimizer=False)
    owner = await _mk_user(db, "auto3@h.io")
    skill = await _mk_skill(db, owner)
    for i in range(2):
        m = await _mk_firing_message(db, owner)
        db.add(SkillFiring(
            skill_id=skill.id, message_id=m.id,
            conversation_id=m.conversation_id, trigger_query_excerpt=f"q{i}",
        ))
    await db.flush()

    assert await evolution_service.should_trigger_skill(db, skill.id) is False


@pytest.mark.asyncio
async def test_auto_trigger_skips_below_sample_threshold(db, monkeypatch):
    _enable_auto(monkeypatch)
    owner = await _mk_user(db, "auto4@h.io")
    skill = await _mk_skill(db, owner)
    m = await _mk_firing_message(db, owner)
    db.add(SkillFiring(
        skill_id=skill.id, message_id=m.id,
        conversation_id=m.conversation_id, trigger_query_excerpt="q",
    ))
    await db.flush()

    assert await evolution_service.should_trigger_skill(db, skill.id) is False


@pytest.mark.asyncio
async def test_auto_trigger_profile_flow(db, monkeypatch):
    _enable_auto(monkeypatch)
    from app.db.models.agent import Profile
    owner = await _mk_user(db, "auto5@h.io")
    profile = Profile(
        id=uuid.uuid4(), name="自动助手", handle="auto-assistant",
        default_agent_id="hermes", system_prompt="基线", scope="personal",
    )
    db.add(profile)
    await db.flush()
    for i in range(2):
        m = await _mk_firing_message(db, owner)
        db.add(ProfileFiring(
            profile_id=profile.id, message_id=m.id,
            conversation_id=m.conversation_id, trigger_query_excerpt=f"q{i}",
        ))
    await db.flush()

    assert await evolution_service.should_trigger_profile(db, profile.id) is True


# ── auto-apply (runner paths) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_proposal_auto_applied_when_real_optimizer(monkeypatch):
    """With auto mode + real optimizer on, a proposal that passes the gates is
    applied automatically through the review path (reviewer=None).

    handle_skill_evolution opens its OWN async_session_maker() session, so the
    skill must be genuinely committed (mirror of test_skill_evolution_d1's
    real_db fixture)."""
    from unittest import mock
    from sqlalchemy import delete, select

    from app.core.security import hash_password
    from app.db.base import async_session_maker
    from app.db.models.skill_evolution import SkillProposal
    from app.db.models.user import User
    from app.services import memory_service
    from agent_runner import runner_skill_evolution as rse
    from skill_evolution.optimizer import EvolutionGateFailure

    _enable_auto(monkeypatch)

    session = async_session_maker()
    try:
        owner = User(
            id=uuid.uuid4(), email="auto6@h.io", name="auto6",
            password_hash=hash_password("Test@1234"), is_active=True, role="member",
        )
        session.add(owner)
        await session.commit()
        skill = await memory_service.create_skill(
            session, name="自动应用技能", description="测试",
            content="基线内容", trigger_conditions={"keywords": ["演化"]},
            owner_id=owner.id,
        )

        class _Result:
            proposed_content = "优化后的内容"
            rationale = "自动演化"
            eval_score_before = 0.5
            eval_score_after = 0.7
            diff_ratio = 0.1
            dataset_summary = {"real_count": 2, "synthetic_count": 0}

        async def _fake_run_evolution(db_, skill_):
            return _Result()

        fake_redis = mock.AsyncMock()
        monkeypatch.setattr(rse, "run_evolution", _fake_run_evolution)
        monkeypatch.setattr(rse.R, "get_redis", lambda: fake_redis)

        await rse.handle_skill_evolution(
            {"skill_id": str(skill.id)}, agents={},
        )

        async with async_session_maker() as db:
            row = (await db.execute(
                select(SkillProposal).where(SkillProposal.skill_id == skill.id)
            )).scalars().first()
            assert row is not None
            assert row.status == "approved"
            assert row.review_note == "auto-applied"
            assert row.reviewed_by is None
            # The single write-back path updated the live skill content.
            fresh = await memory_service.get_skill(db, skill.id)
            assert fresh.content == "优化后的内容"
        assert EvolutionGateFailure is not None
    finally:
        await session.execute(delete(User).where(User.id == owner.id))
        await session.commit()
        await session.close()
