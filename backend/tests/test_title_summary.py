"""First-turn title summary: summarizer title generation + guarded update.

Covers:
- summarize_title_sync (config guard, cleanup, mocked-dspy success/failure)
- Runner._update_conv_title_guarded (placeholder-only update semantics)
- Runner._spawn_title_summary (end-to-end: title written + session_info event
  published; user-renamed conversations are never overwritten)
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.core.security import hash_password
from app.db.models.conversation import Conversation
from app.db.models.user import User


class TestSummarizeTitleSync:
    def test_returns_none_without_auxiliary_config(self, monkeypatch):
        from app.services.summarizer import summarize_title_sync
        monkeypatch.setattr(settings, "auxiliary_llm_model", "")
        monkeypatch.setattr(settings, "auxiliary_llm_api_key", "")
        assert summarize_title_sync("你好", "回复") is None

    def test_returns_none_on_empty_user_text(self, monkeypatch):
        from app.services.summarizer import summarize_title_sync
        monkeypatch.setattr(settings, "auxiliary_llm_model", "openai/test")
        monkeypatch.setattr(settings, "auxiliary_llm_api_key", "key")
        assert summarize_title_sync("   ", "回复") is None

    def test_success_with_mocked_dspy(self, monkeypatch):
        from app.services import summarizer
        monkeypatch.setattr(settings, "auxiliary_llm_model", "openai/test")
        monkeypatch.setattr(settings, "auxiliary_llm_api_key", "key")

        fake_result = MagicMock()
        fake_result.title = "机器人行业调研报告"
        with patch("dspy.LM", return_value=MagicMock()), \
             patch("dspy.Predict", return_value=MagicMock(return_value=fake_result)), \
             patch("dspy.context"):
            title = summarizer.summarize_title_sync(
                "帮我调研中国机器人行业的现状", "以下是调研报告概要……",
            )
        assert title == "机器人行业调研报告"

    def test_strips_surrounding_quotes_and_punctuation(self, monkeypatch):
        from app.services import summarizer
        monkeypatch.setattr(settings, "auxiliary_llm_model", "openai/test")
        monkeypatch.setattr(settings, "auxiliary_llm_api_key", "key")

        fake_result = MagicMock()
        fake_result.title = "「周报模板整理。」"
        with patch("dspy.LM", return_value=MagicMock()), \
             patch("dspy.Predict", return_value=MagicMock(return_value=fake_result)), \
             patch("dspy.context"):
            title = summarizer.summarize_title_sync("帮我整理周报", "好的")
        assert title == "周报模板整理"

    def test_returns_none_on_dspy_failure(self, monkeypatch):
        from app.services import summarizer
        monkeypatch.setattr(settings, "auxiliary_llm_model", "openai/test")
        monkeypatch.setattr(settings, "auxiliary_llm_api_key", "key")

        with patch("dspy.LM", side_effect=RuntimeError("api down")):
            assert summarizer.summarize_title_sync("你好", "回复") is None


async def _mk_convo(title: str) -> str:
    """Create a conversation with REAL commits (async_session_maker).

    The db fixture wraps the session in an outer transaction that never
    commits, so rows created there are invisible to the runner's independent
    sessions (the guarded UPDATE would find nothing). Same pattern as
    test_skill_sync_multiprofile.
    """
    from app.db.base import async_session_maker
    async with async_session_maker() as s:
        user = User(
            id=uuid.uuid4(), email=f"title-{uuid.uuid4().hex[:8]}@h.io", name="t",
            password_hash=hash_password("Test@1234"), is_active=True, role="member",
        )
        s.add(user)
        await s.commit()
    async with async_session_maker() as s:
        convo = Conversation(
            id=uuid.uuid4(), owner_id=user.id, title=title, type="personal",
            primary_agent_id="hermes",
        )
        s.add(convo)
        await s.commit()
        return str(convo.id)


async def _get_title(convo_id: str) -> str:
    from app.db.base import async_session_maker
    from sqlalchemy import select
    async with async_session_maker() as s:
        row = (await s.execute(
            select(Conversation).where(Conversation.id == uuid.UUID(convo_id))
        )).scalar_one()
        return row.title


@pytest.mark.asyncio
async def test_guarded_update_replaces_new_conversation_placeholder():
    from agent_runner.runner import Runner
    cid = await _mk_convo("新会话")
    ok = await Runner()._update_conv_title_guarded(cid, "新标题", "帮我写周报模板" * 5)
    assert ok is True
    assert await _get_title(cid) == "新标题"


@pytest.mark.asyncio
async def test_guarded_update_replaces_truncated_placeholder():
    from agent_runner.runner import Runner
    first_msg = "请帮我分析这份季度销售数据并给出结论"
    cid = await _mk_convo(first_msg[:40])
    ok = await Runner()._update_conv_title_guarded(cid, "销售数据分析", first_msg[:40])
    assert ok is True
    assert await _get_title(cid) == "销售数据分析"


@pytest.mark.asyncio
async def test_guarded_update_never_touches_user_renamed_conversation():
    from agent_runner.runner import Runner
    cid = await _mk_convo("我手动起的名字")
    ok = await Runner()._update_conv_title_guarded(cid, "AI 标题", "别的占位")
    assert ok is False
    assert await _get_title(cid) == "我手动起的名字"


@pytest.mark.asyncio
async def test_spawn_title_summary_publishes_event_and_updates_title(monkeypatch):
    from agent_runner import runner as runner_mod

    first_msg = "帮我调研机器人行业的现状与前景"
    cid = await _mk_convo(first_msg)
    published: list[tuple[str, dict]] = []
    publish_mock = AsyncMock(side_effect=lambda cid_, ev: published.append((cid_, ev)))
    monkeypatch.setattr(runner_mod.R, "publish_event", publish_mock)

    def fake_summary(user_text, reply_text):
        # Real summarize_title_sync is synchronous (runs via asyncio.to_thread).
        return "机器人行业调研"

    monkeypatch.setattr(
        "app.services.summarizer.summarize_title_sync", fake_summary,
    )

    import asyncio as _aio
    runner = runner_mod.Runner()
    runner._spawn_title_summary(cid, user_excerpt=first_msg, reply_text="以下是调研报告……")
    for _ in range(40):
        await _aio.sleep(0.05)
        if published:
            break

    assert await _get_title(cid) == "机器人行业调研"
    assert published and published[0][1]["type"] == "session_info"
    assert published[0][1]["title"] == "机器人行业调研"


@pytest.mark.asyncio
async def test_spawn_title_summary_skips_when_llm_unavailable(monkeypatch):
    from agent_runner import runner as runner_mod

    cid = await _mk_convo("新会话")
    publish_mock = AsyncMock()
    monkeypatch.setattr(runner_mod.R, "publish_event", publish_mock)
    # summarize_title_sync returns None (no auxiliary LLM configured path).
    def fake_summary(user_text, reply_text):
        return None
    monkeypatch.setattr(
        "app.services.summarizer.summarize_title_sync", fake_summary,
    )

    runner_mod.Runner()._spawn_title_summary(cid, user_excerpt="你好", reply_text="回复")
    await __import__("asyncio").sleep(0.2)

    assert await _get_title(cid) == "新会话"
    publish_mock.assert_not_called()
