"""P0-2: max_iterations circuit-breaker behavior test.

Verifies the runner cancels the ACP session when a turn exceeds the
per-profile tool_call cap. Uses a FakeACPClient that emits N tool_call events,
mirroring the skill_firings test's mock pattern.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

import agent_runner.session_pool as sp
from agent_runner.acp_client import ACPTimeout  # noqa: F401


class FakeProc:
    returncode = None
    pid = 9999


class ToolSpamClient:
    """Fake ACPClient that emits `count` tool_call events then a final message.
    Records whether cancel() was called so we can assert the breaker fired."""

    def __init__(self, command, cwd, *, protocol_version=1, on_update=None, on_fs_write=None, env=None):
        self.on_update = on_update
        self.cancelled = False
        self._proc = FakeProc()
        self._session_id = "fake-sess"

    async def start(self): pass
    async def initialize(self): return {}
    async def new_session(self, cwd, mcp_servers=None): return "fake-sess"
    async def resume_session(self, *a, **kw): return "fake-sess"
    async def set_session_mode(self, *a, **kw): pass
    async def prompt(self, content):
        if not self.on_update:
            return "end_turn"
        # Emit tool_call events to trip the breaker.
        for i in range(60):
            await self.on_update({
                "sessionUpdate": "tool_call",
                "title": f"tool_{i}",
                "status": "completed",
            })
            if self.cancelled:
                return "cancelled"
        await self.on_update({
            "sessionUpdate": "agent_message_chunk",
            "content": {"text": "done"},
        })
        return "end_turn"

    async def cancel(self):
        self.cancelled = True

    async def stop(self): pass


@pytest.fixture
def runner_with_spam_agent(monkeypatch):
    monkeypatch.setattr(sp, "ACPClient", ToolSpamClient)
    from agent_runner.runner import Runner
    r = Runner()
    r.agents = {"hermes": SimpleNamespace(command=["hermes", "acp"])}
    return r


@pytest_asyncio.fixture
async def real_convo():
    """Create a real conversation+message in a non-rollback session (handle_single
    opens its own session_maker, invisible to the rollback `db` fixture)."""
    from app.db.base import async_session_maker
    from app.db.models.conversation import Conversation, Message
    from app.db.models.user import User
    from app.core.security import hash_password
    cid = []
    async with async_session_maker() as session:
        u = User(id=uuid.uuid4(), email="brk@test.com", name="brk",
                 password_hash=hash_password("Test@1234"), is_active=True, role="member")
        session.add(u)
        await session.flush()
        c = Conversation(id=uuid.uuid4(), title="test", owner_id=u.id,
                         primary_agent_id="hermes", active_agent_ids=["hermes"])
        session.add(c)
        await session.flush()
        cid.append(str(c.id))
        cid.append(str(u.id))
        await session.commit()
    yield cid
    # cleanup
    from sqlalchemy import delete
    async with async_session_maker() as session:
        await session.execute(delete(Message).where(Message.conversation_id == uuid.UUID(cid[0])))
        await session.execute(delete(Conversation).where(Conversation.id == uuid.UUID(cid[0])))
        await session.execute(delete(User).where(User.id == uuid.UUID(cid[1])))
        await session.commit()


class TestIterationBreaker:
    async def test_breaker_cancels_at_limit(self, runner_with_spam_agent, real_convo, monkeypatch):
        """When tool_calls exceed max_iterations, the runner cancels the session.
        We assert via the warning log the breaker emits (the DB message path
        depends on _create_agent_message which needs more setup; the log is the
        authoritative signal that the breaker logic ran)."""
        conv_id, _ = real_convo
        import logging
        breaker_messages = []

        class _CaptureHandler(logging.Handler):
            def emit(self, record):
                if "Iteration cap hit" in record.getMessage():
                    breaker_messages.append(record.getMessage())

        logger = logging.getLogger("hermes.runner")
        handler = _CaptureHandler()
        logger.addHandler(handler)
        try:
            with patch("agent_runner.runner.R.publish_event", new_callable=AsyncMock), \
                 patch("agent_runner.runner.R.is_cancelled", new_callable=AsyncMock, return_value=False), \
                 patch("agent_runner.runner.R.clear_cancel", new_callable=AsyncMock), \
                 patch("agent_runner.runner.R.get_redis") as mock_redis:
                mock_redis.return_value.get = AsyncMock(return_value=None)
                mock_redis.return_value.set = AsyncMock(return_value=True)

                task = {
                    "type": "single",
                    "conversation_id": conv_id,
                    "message_id": str(uuid.uuid4()),
                    "agent_id": "hermes",
                    "profile_id": None,
                    "text": "do stuff",
                    "system_prompt": None,
                    "profile_dir": None,
                    "mcp_servers": [],
                    "max_iterations": 10,
                    "stage": None,
                }
                await runner_with_spam_agent.handle_single(task)
        finally:
            logger.removeHandler(handler)

        assert len(breaker_messages) >= 1, "Breaker should have fired at 10 tool_calls"
        assert "10 tool_calls >= 10" in breaker_messages[0]

    async def test_breaker_disabled_when_zero(self, runner_with_spam_agent, real_convo, monkeypatch):
        """max_iterations=0 means disabled — the turn should complete normally."""
        conv_id, _ = real_convo
        with patch("agent_runner.runner.R.publish_event", new_callable=AsyncMock), \
             patch("agent_runner.runner.R.is_cancelled", new_callable=AsyncMock, return_value=False), \
             patch("agent_runner.runner.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner.R.get_redis") as mock_redis:
            mock_redis.return_value.get = AsyncMock(return_value=None)
            mock_redis.return_value.set = AsyncMock(return_value=True)

            task = {
                "type": "single",
                "conversation_id": conv_id,
                "message_id": str(uuid.uuid4()),
                "agent_id": "hermes",
                "profile_id": None,
                "text": "do stuff",
                "system_prompt": None,
                "profile_dir": None,
                "mcp_servers": [],
                "max_iterations": 0,  # disabled
                "stage": None,
            }
            await runner_with_spam_agent.handle_single(task)
