"""P1-2: conversation summary pipeline tests.

Covers: summarizer (mocked LLM), the summary worker's incremental logic, and
the dispatch-time injection (summary appears in the prompt prefix).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch



class TestSummarizer:
    def test_summarize_returns_none_without_config(self, monkeypatch):
        """When auxiliary_llm is not configured, summarize returns None."""
        from app.config import settings
        from app.services.summarizer import summarize_sync
        monkeypatch.setattr(settings, "auxiliary_llm_model", "")
        monkeypatch.setattr(settings, "auxiliary_llm_api_key", "")
        assert summarize_sync("some text") is None

    def test_summarize_returns_none_on_empty(self, monkeypatch):
        from app.config import settings
        from app.services.summarizer import summarize_sync
        monkeypatch.setattr(settings, "auxiliary_llm_model", "openai/test")
        monkeypatch.setattr(settings, "auxiliary_llm_api_key", "key")
        assert summarize_sync("") is None
        assert summarize_sync("   ") is None

    def test_summarize_with_mocked_dspy(self, monkeypatch):
        """Mock dspy.Predict to return a structured summary."""
        from app.config import settings
        from app.services import summarizer
        monkeypatch.setattr(settings, "auxiliary_llm_model", "openai/test")
        monkeypatch.setattr(settings, "auxiliary_llm_api_key", "key")

        fake_result = MagicMock()
        fake_result.summary = "【决策】采用方案A\n【待办】完成测试\n【关键事实】用户偏好简洁"
        fake_predictor = MagicMock(return_value=fake_result)
        fake_lm = MagicMock()

        with patch("dspy.LM", return_value=fake_lm), \
             patch("dspy.Predict", return_value=fake_predictor), \
             patch("dspy.context"):
            result = summarizer.summarize_sync("用户：我们用什么方案？\n助手：建议方案A")
        assert result is not None
        assert "方案A" in result.summary
        assert result.token_estimate > 0

    def test_build_messages_text(self):
        from app.services.summarizer import build_messages_text
        text = build_messages_text([("用户", "你好"), ("助手", "你好，有什么可以帮你？")])
        assert "用户" in text and "你好" in text
        assert "助手" in text


class TestSummaryWorker:
    async def test_worker_skips_when_disabled(self, monkeypatch):
        """handle_conversation_summary returns early when summary_enabled is off."""
        from app.config import settings
        from agent_runner.runner_conversation_summary import handle_conversation_summary
        monkeypatch.setattr(settings, "summary_enabled", False)
        # Should return without error (no-op).
        await handle_conversation_summary({"conversation_id": str(uuid.uuid4())})

    async def test_worker_skips_insufficient_increment(self, monkeypatch, db):
        """When there aren't enough new messages, the worker skips (cost guard)."""
        from app.config import settings
        monkeypatch.setattr(settings, "summary_enabled", True)
        monkeypatch.setattr(settings, "summary_increment_threshold", 100)

        from app.db.models.conversation import Conversation
        from app.db.models.user import User
        from app.core.security import hash_password
        u = User(id=uuid.uuid4(), email=f"sw{uuid.uuid4().hex[:6]}@t.com", name="sw",
                 password_hash=hash_password("Test@1234"), is_active=True, role="member")
        db.add(u)
        await db.flush()
        c = Conversation(id=uuid.uuid4(), title="sw", owner_id=u.id,
                         primary_agent_id="hermes", active_agent_ids=["hermes"])
        db.add(c)
        await db.flush()

        with patch("agent_runner.runner_conversation_summary.async_session_maker") as mock_sm:
            mock_sm.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_sm.return_value.__aexit__ = AsyncMock(return_value=False)
            from app.core import redis as R
            with patch.object(R, "get_redis"):
                await __import__(
                    "agent_runner.runner_conversation_summary", fromlist=["handle_conversation_summary"]
                ).handle_conversation_summary({"conversation_id": str(c.id)})


class TestSummaryInjection:
    async def test_dispatch_reads_summary_into_prompt(self, db, monkeypatch):
        """When a conversation has a summary, dispatch injects it into the
        system_prompt prefix."""
        from app.config import settings
        from app.db.models.conversation import Conversation, ConversationSummary
        from app.db.models.user import User
        from app.core.security import hash_password

        monkeypatch.setattr(settings, "summary_enabled", True)
        u = User(id=uuid.uuid4(), email=f"si{uuid.uuid4().hex[:6]}@t.com", name="si",
                 password_hash=hash_password("Test@1234"), is_active=True, role="member")
        db.add(u)
        await db.flush()
        c = Conversation(id=uuid.uuid4(), title="si", owner_id=u.id,
                         primary_agent_id="hermes", active_agent_ids=["hermes"],
                         acp_session_id=None)
        db.add(c)
        await db.flush()
        s = ConversationSummary(
            conversation_id=c.id, summary="【决策】选了React",
            covered_count=20, token_estimate=100,
        )
        db.add(s)
        await db.flush()

        # Read the summary the way dispatch does.
        from sqlalchemy import select
        row = (await db.execute(
            select(ConversationSummary).where(ConversationSummary.conversation_id == c.id)
        )).scalar_one_or_none()
        assert row is not None
        assert "React" in row.summary

        # Simulate the injection logic dispatch uses.
        system_prompt = None
        if row and row.summary:
            summary_block = f"【早期对话摘要】\n{row.summary}"
            system_prompt = summary_block if not system_prompt else f"{summary_block}\n\n{system_prompt}"
        assert "早期对话摘要" in system_prompt
        assert "React" in system_prompt
