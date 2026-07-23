"""P2-3: tool risk-guard runner behavior test.

Verifies that when a tool_call's title matches a high-risk MCP server name and
the tool isn't authorised, the runner cancels the session and emits a
tool_blocked event. Also verifies that an authorised tool is allowed through.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

import agent_runner.session_pool as sp


class FakeProc:
    returncode = None
    pid = 7777


class RiskToolClient:
    """Fake ACPClient that emits one tool_call with a configurable title."""

    def __init__(self, command, cwd, *, protocol_version=1, on_update=None, on_fs_write=None, env=None):
        self.on_update = on_update
        self.cancelled = False
        self.tool_title = "risky-mcp-server/delete-file"
        self._proc = FakeProc()

    async def start(self): pass
    async def initialize(self): return {}
    async def new_session(self, cwd, mcp_servers=None): return "risk-sess"
    async def resume_session(self, *a, **kw): return "risk-sess"
    async def set_session_mode(self, *a, **kw): pass
    async def prompt(self, content):
        if self.on_update:
            await self.on_update({
                "sessionUpdate": "tool_call",
                "title": self.tool_title,
                "status": "running",
            })
            if self.cancelled:
                return "cancelled"
            await self.on_update({
                "sessionUpdate": "agent_message_chunk",
                "content": {"text": "proceeded anyway"},
            })
        return "end_turn"

    async def cancel(self):
        self.cancelled = True

    async def stop(self): pass


@pytest.fixture
def runner_with_risk(monkeypatch):
    monkeypatch.setattr(sp, "ACPClient", RiskToolClient)
    from agent_runner.runner import Runner
    r = Runner()
    r.agents = {"hermes": SimpleNamespace(command=["hermes", "acp"])}
    return r


@pytest_asyncio.fixture
async def risk_convo():
    from app.db.base import async_session_maker
    from app.db.models.conversation import Conversation, Message
    from app.db.models.user import User
    from app.core.security import hash_password
    from sqlalchemy import delete
    ids = []
    async with async_session_maker() as s:
        u = User(id=uuid.uuid4(), email=f"risk{uuid.uuid4().hex[:6]}@t.com", name="rk",
                 password_hash=hash_password("Test@1234"), is_active=True, role="member")
        s.add(u)
        await s.flush()
        c = Conversation(id=uuid.uuid4(), title="risk", owner_id=u.id,
                         primary_agent_id="hermes", active_agent_ids=["hermes"])
        s.add(c)
        await s.commit()
        ids.extend([str(c.id), str(u.id)])
    yield ids
    async with async_session_maker() as s:
        await s.execute(delete(Message).where(Message.conversation_id == uuid.UUID(ids[0])))
        await s.execute(delete(Conversation).where(Conversation.id == uuid.UUID(ids[0])))
        await s.execute(delete(User).where(User.id == uuid.UUID(ids[1])))
        await s.commit()


def _run_task(conv_id, **kw):
    return {
        "type": "single", "conversation_id": conv_id, "message_id": str(uuid.uuid4()),
        "agent_id": "hermes", "profile_id": None, "text": "use risky tool",
        "system_prompt": None, "profile_dir": None, "mcp_servers": [],
        "max_iterations": 0, "stage": None, **kw,
    }


class TestToolRiskGuard:
    async def test_unauthorised_high_risk_blocked(self, runner_with_risk, risk_convo):
        """A high-risk tool_call that isn't authorised should be blocked (cancel)."""
        conv_id, _ = risk_convo
        blocked_events = []

        async def capture_publish(cid, event):
            if event.get("type") == "tool_blocked":
                blocked_events.append(event)

        with patch("agent_runner.runner.R.publish_event", side_effect=capture_publish), \
             patch("agent_runner.runner.R.is_cancelled", new_callable=AsyncMock, return_value=False), \
             patch("agent_runner.runner.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner.R.get_redis") as mock_redis, \
             patch.object(runner_with_risk, "_load_high_risk_server_names", return_value={"risky-mcp-server"}), \
             patch.object(runner_with_risk, "_is_tool_authorised", return_value=False):
            mock_redis.return_value.get = AsyncMock(return_value=None)
            mock_redis.return_value.set = AsyncMock(return_value=True)
            await runner_with_risk.handle_single(_run_task(conv_id))

        assert len(blocked_events) >= 1, "Should have emitted a tool_blocked event"
        assert blocked_events[0]["tool"] == "risky-mcp-server"

    async def test_authorised_high_risk_allowed(self, runner_with_risk, risk_convo):
        """A high-risk tool that IS authorised should not be blocked."""
        conv_id, _ = risk_convo
        blocked_events = []

        async def capture_publish(cid, event):
            if event.get("type") == "tool_blocked":
                blocked_events.append(event)

        with patch("agent_runner.runner.R.publish_event", side_effect=capture_publish), \
             patch("agent_runner.runner.R.is_cancelled", new_callable=AsyncMock, return_value=False), \
             patch("agent_runner.runner.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner.R.get_redis") as mock_redis, \
             patch.object(runner_with_risk, "_load_high_risk_server_names", return_value={"risky-mcp-server"}), \
             patch.object(runner_with_risk, "_is_tool_authorised", return_value=True):
            mock_redis.return_value.get = AsyncMock(return_value=None)
            mock_redis.return_value.set = AsyncMock(return_value=True)
            await runner_with_risk.handle_single(_run_task(conv_id))

        assert len(blocked_events) == 0, "Authorised tool should NOT be blocked"

    async def test_low_risk_not_checked(self, runner_with_risk, risk_convo):
        """When no high-risk servers are configured, no blocking occurs."""
        conv_id, _ = risk_convo
        blocked_events = []

        async def capture_publish(cid, event):
            if event.get("type") == "tool_blocked":
                blocked_events.append(event)

        with patch("agent_runner.runner.R.publish_event", side_effect=capture_publish), \
             patch("agent_runner.runner.R.is_cancelled", new_callable=AsyncMock, return_value=False), \
             patch("agent_runner.runner.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner.R.get_redis") as mock_redis, \
             patch.object(runner_with_risk, "_load_high_risk_server_names", return_value=set()):
            mock_redis.return_value.get = AsyncMock(return_value=None)
            mock_redis.return_value.set = AsyncMock(return_value=True)
            await runner_with_risk.handle_single(_run_task(conv_id))

        assert len(blocked_events) == 0
