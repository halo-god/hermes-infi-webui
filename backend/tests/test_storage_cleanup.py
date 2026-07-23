"""P1-2/P2-3/P2-4/file: integration tests for the data-layer pieces that
were previously only verified by ad-hoc scripts.

Covers: object-storage cleanup on delete, conversation summary CRUD, profile
prompt proposal review, and the stage/authorise-tool API endpoints.
"""
from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def team_knowledge_with_storage(db: AsyncSession):
    """A knowledge item with a storage_key (simulating an offloaded file)."""
    from app.db.models.team import Team
    from app.db.models.user import User
    from app.core.security import hash_password
    u = User(id=uuid.uuid4(), email="stor@test.com", name="stor",
             password_hash=hash_password("Test@1234"), is_active=True, role="member")
    db.add(u)
    await db.flush()
    t = Team(name="stor-team")
    db.add(t)
    await db.flush()
    return t.id, u.id


# ── P1-2: conversation summary ──

class TestConversationSummary:
    async def test_create_and_read_summary(self, db: AsyncSession):
        from app.db.models.conversation import Conversation, ConversationSummary
        from app.db.models.user import User
        from app.core.security import hash_password
        u = User(id=uuid.uuid4(), email="sum@test.com", name="sum",
                 password_hash=hash_password("Test@1234"), is_active=True, role="member")
        db.add(u)
        await db.flush()
        c = Conversation(id=uuid.uuid4(), title="s", owner_id=u.id,
                         primary_agent_id="hermes", active_agent_ids=["hermes"])
        db.add(c)
        await db.flush()
        s = ConversationSummary(
            conversation_id=c.id, summary="讨论了产品需求和技术方案",
            covered_count=15, token_estimate=200,
        )
        db.add(s)
        await db.flush()
        assert s.id is not None
        assert s.summary == "讨论了产品需求和技术方案"
        assert s.covered_count == 15


# ── P2-4: profile prompt proposal review ──
# These use real_db (not the rollback `db` fixture) because review_proposal
# commits internally, which breaks the rollback isolation.

@pytest_asyncio.fixture
async def real_db():
    from app.db.base import async_session_maker
    from sqlalchemy import delete
    from app.db.models.user import User
    from app.db.models.agent import Profile
    from app.db.models.profile_evolution import ProfilePromptProposal
    from app.core.security import hash_password
    session = async_session_maker()
    reviewer = User(id=uuid.uuid4(), email=f"rev{uuid.uuid4().hex[:6]}@test.com", name="rev",
                    password_hash=hash_password("Test@1234"), is_active=True, role="member")
    session.add(reviewer)
    await session.commit()
    created = {"profiles": [], "users": [reviewer.id]}
    try:
        yield session, created, reviewer.id
    finally:
        await session.execute(delete(ProfilePromptProposal))
        await session.execute(delete(Profile).where(Profile.id.in_(created["profiles"])) if created["profiles"] else delete(Profile).where(False))
        await session.execute(delete(User).where(User.id == reviewer.id))
        await session.commit()
        await session.close()


class TestProfileProposalReview:
    async def test_approved_writes_back_system_prompt(self, real_db):
        from app.db.models.agent import Profile
        from app.db.models.profile_evolution import ProfilePromptProposal
        from app.services import profile_evolution_service
        session, created, reviewer_id = real_db
        p = Profile(name="pp", handle=f"pp{uuid.uuid4().hex[:6]}", scope="personal", system_prompt="原始提示词")
        session.add(p)
        await session.flush()
        created["profiles"].append(p.id)
        prop = ProfilePromptProposal(
            profile_id=p.id, proposed_prompt="优化后的提示词",
            eval_score_before=0.5, eval_score_after=0.75, diff_ratio=0.3,
            dataset_summary={"real_count": 5},
        )
        session.add(prop)
        await session.flush()

        updated = await profile_evolution_service.review_proposal(
            session, prop, reviewer_id=reviewer_id, status="approved", review_note="good",
        )
        assert updated.status == "approved"
        await session.refresh(p)
        assert p.system_prompt == "优化后的提示词"

    async def test_rejected_does_not_write_back(self, real_db):
        from app.db.models.agent import Profile
        from app.db.models.profile_evolution import ProfilePromptProposal
        from app.services import profile_evolution_service
        session, created, reviewer_id = real_db
        p = Profile(name="pp2", handle=f"pp2{uuid.uuid4().hex[:6]}", scope="personal", system_prompt="保持不变")
        session.add(p)
        await session.flush()
        created["profiles"].append(p.id)
        prop = ProfilePromptProposal(
            profile_id=p.id, proposed_prompt="不该被采用",
            eval_score_before=0.5, eval_score_after=0.55, diff_ratio=0.1,
            dataset_summary={},
        )
        session.add(prop)
        await session.flush()

        await profile_evolution_service.review_proposal(
            session, prop, reviewer_id=reviewer_id, status="rejected", review_note="no",
        )
        await session.refresh(p)
        assert p.system_prompt == "保持不变"


# ── P2-file: object storage cleanup on delete ──

class TestStorageCleanupOnDelete:
    async def test_delete_knowledge_calls_storage_delete(
        self, db: AsyncSession, team_knowledge_with_storage, monkeypatch,
    ):
        """delete_knowledge should call object_storage.delete for the storage_key."""
        from app.db.models.team import TeamKnowledge
        from app.services import team_service
        team_id, _ = team_knowledge_with_storage
        k = TeamKnowledge(
            team_id=team_id, name="offloaded", kind="docx",
            content="<p>preview</p>", storage_key="team-knowledge/x/file.docx",
        )
        db.add(k)
        await db.flush()

        deleted_keys = []

        async def fake_cleanup(storage_key):
            deleted_keys.append(storage_key)

        monkeypatch.setattr(team_service, "_cleanup_storage", fake_cleanup)
        await team_service.delete_knowledge(db, team_id, k.id)
        assert "team-knowledge/x/file.docx" in deleted_keys

    async def test_delete_knowledge_without_storage_key_no_cleanup(
        self, db: AsyncSession, team_knowledge_with_storage, monkeypatch,
    ):
        from app.db.models.team import TeamKnowledge
        from app.services import team_service
        team_id, _ = team_knowledge_with_storage
        k = TeamKnowledge(
            team_id=team_id, name="inline", kind="txt",
            content="inline text", storage_key=None,
        )
        db.add(k)
        await db.flush()

        deleted_keys = []

        async def fake_cleanup(storage_key):
            deleted_keys.append(storage_key)

        monkeypatch.setattr(team_service, "_cleanup_storage", fake_cleanup)
        await team_service.delete_knowledge(db, team_id, k.id)
        assert len(deleted_keys) == 0


# ── P2-3: tool risk guard helpers ──

class TestToolRiskGuard:
    async def test_is_tool_authorised_false_by_default(self, monkeypatch):
        from agent_runner.runner import Runner
        from unittest.mock import AsyncMock, patch
        r = Runner.__new__(Runner)
        with patch("agent_runner.runner.R.get_redis") as mock_redis:
            mock_redis.return_value.get = AsyncMock(return_value=None)
            result = await r._is_tool_authorised("conv-x", "dangerous-tool")
        assert result is False

    async def test_is_tool_authorised_true_when_cached(self, monkeypatch):
        from agent_runner.runner import Runner
        from unittest.mock import AsyncMock, patch
        r = Runner.__new__(Runner)
        with patch("agent_runner.runner.R.get_redis") as mock_redis:
            mock_redis.return_value.get = AsyncMock(return_value=b"1")
            result = await r._is_tool_authorised("conv-x", "dangerous-tool")
        assert result is True
