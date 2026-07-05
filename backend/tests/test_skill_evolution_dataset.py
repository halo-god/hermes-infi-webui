"""Eval-dataset builder for the self-evolving skills pipeline."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.core.security import create_token, hash_password
from app.db.models.conversation import Conversation, Message
from app.db.models.skill_evolution import SkillFiring
from app.db.models.user import User
from app.services import memory_service
from skill_evolution.dataset import DatasetExample, build_dataset, build_real_examples


async def _mk_user(db, email: str) -> User:
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
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


async def _mk_firing(
    db, skill, convo, *, reply_text: str, reactions: dict | None = None, query: str = "限流问题",
    created_at: datetime | None = None,
):
    msg = Message(
        conversation_id=convo.id, role="agent", agent_id="hermes",
        content={"text": reply_text}, status="complete", reactions=reactions or {},
    )
    db.add(msg)
    await db.flush()
    firing = SkillFiring(
        skill_id=skill.id, message_id=msg.id, conversation_id=convo.id,
        owner_id=convo.owner_id, trigger_query_excerpt=query,
    )
    if created_at is not None:
        # Postgres func.now() (the model's server_default) returns the same
        # value for every statement within one transaction, so firings
        # created back-to-back in a single test transaction would otherwise
        # tie on created_at — pass explicit, distinct timestamps whenever a
        # test asserts on ordering.
        firing.created_at = created_at
    db.add(firing)
    await db.flush()
    return firing, msg


async def _mk_conversation(db, owner: User) -> Conversation:
    convo = Conversation(owner_id=owner.id, title="会话", primary_agent_id="hermes")
    db.add(convo)
    await db.flush()
    return convo


async def test_build_real_examples_labels_from_reactions(db):
    owner = await _mk_user(db, "ds-owner@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    await _mk_firing(db, skill, convo, reply_text="检查限流键", reactions={"👍": [str(owner.id)]})
    await _mk_firing(db, skill, convo, reply_text="没说到点子上", reactions={"👎": [str(owner.id)]})
    await _mk_firing(db, skill, convo, reply_text="中性回复")

    examples, summary = await build_real_examples(db, skill)

    assert summary.real_count == 3
    labels = {e.output_trace: e.label for e in examples}
    assert labels["检查限流键"] == "positive"
    assert labels["没说到点子上"] == "negative"
    assert labels["中性回复"] is None
    assert all(e.source == "real" for e in examples)
    assert all(e.skill_content_snapshot == skill.content for e in examples)


async def test_build_real_examples_newest_first_and_respects_limit(db):
    owner = await _mk_user(db, "ds-owner2@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    base = datetime.now(tz=timezone.utc)
    for i in range(3):
        await _mk_firing(db, skill, convo, reply_text=f"回复{i}", created_at=base + timedelta(seconds=i))

    examples, summary = await build_real_examples(db, skill, limit=2)

    assert summary.real_count == 2
    # newest-first: the last-created firing (回复2) should come first
    assert examples[0].output_trace == "回复2"


async def test_build_real_examples_skips_empty_replies(db):
    owner = await _mk_user(db, "ds-owner3@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    await _mk_firing(db, skill, convo, reply_text="")
    await _mk_firing(db, skill, convo, reply_text="有内容")

    examples, summary = await build_real_examples(db, skill)

    assert summary.real_count == 1
    assert examples[0].output_trace == "有内容"


async def test_build_real_examples_respects_char_budget(db):
    owner = await _mk_user(db, "ds-owner4@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    await _mk_firing(db, skill, convo, reply_text="a" * 100)
    await _mk_firing(db, skill, convo, reply_text="b" * 100)

    examples, _ = await build_real_examples(db, skill, max_total_chars=150)

    total = sum(len(e.output_trace or "") for e in examples)
    assert total <= 150


async def test_build_dataset_tops_up_with_synthetic_when_sparse(db):
    owner = await _mk_user(db, "ds-owner5@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    await _mk_firing(db, skill, convo, reply_text="仅一条真实记录")

    calls = []

    async def fake_generator(skill_arg, count):
        calls.append((skill_arg.id, count))
        return [
            DatasetExample(
                query="模拟问题", skill_content_snapshot=skill_arg.content,
                output_trace="模拟回复", label=None, source="synthetic",
            )
            for _ in range(count)
        ]

    examples, summary = await build_dataset(
        db, skill, min_real=5, synthetic_count=3, synthetic_generator=fake_generator,
    )

    assert calls == [(skill.id, 3)]
    assert summary.real_count == 1
    assert summary.synthetic_count == 3
    assert len(examples) == 4


async def test_build_dataset_skips_synthetic_when_enough_real(db):
    owner = await _mk_user(db, "ds-owner6@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    for i in range(3):
        await _mk_firing(db, skill, convo, reply_text=f"回复{i}")

    called = False

    async def fake_generator(skill_arg, count):
        nonlocal called
        called = True
        return []

    examples, summary = await build_dataset(db, skill, min_real=2, synthetic_generator=fake_generator)

    assert called is False
    assert summary.real_count == 3
    assert summary.synthetic_count == 0


async def test_build_dataset_real_only_when_no_generator(db):
    owner = await _mk_user(db, "ds-owner7@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    await _mk_firing(db, skill, convo, reply_text="仅一条")

    examples, summary = await build_dataset(db, skill, min_real=5, synthetic_generator=None)

    assert summary.real_count == 1
    assert summary.synthetic_count == 0
    assert len(examples) == 1


# ── API: debug preview endpoint ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def super_admin_user(db) -> User:
    user = User(
        id=uuid.uuid4(), email="root-se@hermes.io", name="Root",
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


async def test_preview_dataset_endpoint(client, super_admin_headers, super_admin_user, db):
    skill = await _mk_skill(db, super_admin_user)
    convo = await _mk_conversation(db, super_admin_user)
    await _mk_firing(db, skill, convo, reply_text="限流建议", reactions={"👍": [str(super_admin_user.id)]})
    await db.commit()

    resp = await client.get(f"/api/v1/skill-evolution/skills/{skill.id}/preview-dataset", headers=super_admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["skill_id"] == str(skill.id)
    assert body["summary"]["real_count"] == 1
    assert body["examples"][0]["output_trace"] == "限流建议"
    assert body["examples"][0]["label"] == "positive"


async def test_preview_dataset_endpoint_forbidden_for_regular_user(client, auth_headers, test_user, db):
    skill = await _mk_skill(db, test_user)
    await db.commit()

    resp = await client.get(f"/api/v1/skill-evolution/skills/{skill.id}/preview-dataset", headers=auth_headers)

    assert resp.status_code == 403
