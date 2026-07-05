"""Stage E: proposal review queue — admin-wide skill listing + approve/reject.

The one invariant every test here ultimately guards: agent_skills.content
only ever changes through the approve path, never as a side effect of
listing or rejecting a proposal.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.core.security import create_token, hash_password
from app.db.models.skill_evolution import SkillProposal
from app.db.models.user import User
from app.services import memory_service, skill_evolution_service


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


async def _mk_proposal(db, skill, **kwargs) -> SkillProposal:
    defaults = dict(
        skill_id=skill.id,
        proposed_content=skill.content + "\n补充说明",
        rationale="测试用提案",
        eval_score_before=0.5, eval_score_after=0.7, diff_ratio=0.1,
        dataset_summary={"real_count": 3, "synthetic_count": 0},
        status="pending",
    )
    defaults.update(kwargs)
    p = SkillProposal(**defaults)
    db.add(p)
    await db.flush()
    return p


# ── service layer ────────────────────────────────────────────────────────────

async def test_review_proposal_approve_writes_skill_content(db):
    owner = await _mk_user(db, "e-owner@h.io")
    skill = await _mk_skill(db, owner)
    original_content = skill.content
    proposal = await _mk_proposal(db, skill)
    reviewer = await _mk_user(db, "e-reviewer@h.io", role="super_admin")

    updated = await skill_evolution_service.review_proposal(
        db, proposal, reviewer_id=reviewer.id, status="approved", review_note="看起来不错",
    )

    assert updated.status == "approved"
    assert updated.reviewed_by == reviewer.id
    assert updated.reviewed_at is not None
    assert updated.review_note == "看起来不错"

    await db.refresh(skill)
    assert skill.content == proposal.proposed_content
    assert skill.content != original_content


async def test_review_proposal_reject_does_not_touch_skill_content(db):
    owner = await _mk_user(db, "e-owner2@h.io")
    skill = await _mk_skill(db, owner)
    original_content = skill.content
    proposal = await _mk_proposal(db, skill)
    reviewer = await _mk_user(db, "e-reviewer2@h.io", role="super_admin")

    await skill_evolution_service.review_proposal(
        db, proposal, reviewer_id=reviewer.id, status="rejected", review_note="内容跑偏了",
    )

    await db.refresh(skill)
    assert skill.content == original_content
    assert proposal.status == "rejected"


async def test_list_proposals_filters_by_status_and_skill(db):
    owner = await _mk_user(db, "e-owner3@h.io")
    skill_a = await _mk_skill(db, owner, name="技能A")
    skill_b = await _mk_skill(db, owner, name="技能B")
    p1 = await _mk_proposal(db, skill_a, status="pending")
    p2 = await _mk_proposal(db, skill_a, status="approved")
    p3 = await _mk_proposal(db, skill_b, status="pending")

    only_pending = await skill_evolution_service.list_proposals(db, status="pending")
    assert {p.id for p in only_pending} == {p1.id, p3.id}

    only_skill_a = await skill_evolution_service.list_proposals(db, skill_id=skill_a.id)
    assert {p.id for p in only_skill_a} == {p1.id, p2.id}


async def test_list_all_skills_spans_owners(db):
    owner1 = await _mk_user(db, "e-owner4@h.io")
    owner2 = await _mk_user(db, "e-owner5@h.io")
    s1 = await _mk_skill(db, owner1, name="技能1")
    s2 = await _mk_skill(db, owner2, name="技能2")

    all_skills = await memory_service.list_all_skills(db)
    ids = {s.id for s in all_skills}
    assert s1.id in ids
    assert s2.id in ids


# ── API layer ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def super_admin_user(db) -> User:
    user = User(
        id=uuid.uuid4(), email="root-e@hermes.io", name="Root",
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


async def test_list_all_skills_endpoint(client, super_admin_headers, super_admin_user, db):
    skill = await _mk_skill(db, super_admin_user)
    await db.commit()

    resp = await client.get("/api/v1/skill-evolution/skills", headers=super_admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert any(s["id"] == str(skill.id) for s in body)


async def test_list_all_skills_endpoint_forbidden_for_regular_user(client, auth_headers):
    resp = await client.get("/api/v1/skill-evolution/skills", headers=auth_headers)
    assert resp.status_code == 403


async def test_list_proposals_endpoint(client, super_admin_headers, super_admin_user, db):
    skill = await _mk_skill(db, super_admin_user)
    proposal = await _mk_proposal(db, skill)
    await db.commit()

    resp = await client.get("/api/v1/skill-evolution/proposals", headers=super_admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert any(p["id"] == str(proposal.id) for p in body)

    resp2 = await client.get(
        "/api/v1/skill-evolution/proposals", params={"status": "approved"}, headers=super_admin_headers,
    )
    assert resp2.json() == []


async def test_review_proposal_endpoint_approve(client, super_admin_headers, super_admin_user, db):
    skill = await _mk_skill(db, super_admin_user)
    proposal = await _mk_proposal(db, skill)
    await db.commit()

    resp = await client.patch(
        f"/api/v1/skill-evolution/proposals/{proposal.id}",
        json={"status": "approved", "review_note": "同意"},
        headers=super_admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["review_note"] == "同意"

    updated_skill = await memory_service.get_skill(db, skill.id)
    assert updated_skill.content == proposal.proposed_content


async def test_review_proposal_endpoint_rejects_already_reviewed(client, super_admin_headers, super_admin_user, db):
    skill = await _mk_skill(db, super_admin_user)
    proposal = await _mk_proposal(db, skill, status="approved")
    await db.commit()

    resp = await client.patch(
        f"/api/v1/skill-evolution/proposals/{proposal.id}",
        json={"status": "rejected"},
        headers=super_admin_headers,
    )
    assert resp.status_code == 409


async def test_review_proposal_endpoint_validates_status(client, super_admin_headers, super_admin_user, db):
    skill = await _mk_skill(db, super_admin_user)
    proposal = await _mk_proposal(db, skill)
    await db.commit()

    resp = await client.patch(
        f"/api/v1/skill-evolution/proposals/{proposal.id}",
        json={"status": "not-a-real-status"},
        headers=super_admin_headers,
    )
    assert resp.status_code == 422


async def test_review_proposal_endpoint_404_for_unknown_proposal(client, super_admin_headers):
    resp = await client.patch(
        f"/api/v1/skill-evolution/proposals/{uuid.uuid4()}",
        json={"status": "approved"},
        headers=super_admin_headers,
    )
    assert resp.status_code == 404


async def test_review_proposal_endpoint_forbidden_for_regular_user(client, auth_headers, test_user, db):
    skill = await _mk_skill(db, test_user)
    proposal = await _mk_proposal(db, skill)
    await db.commit()

    resp = await client.patch(
        f"/api/v1/skill-evolution/proposals/{proposal.id}",
        json={"status": "approved"},
        headers=auth_headers,
    )
    assert resp.status_code == 403
