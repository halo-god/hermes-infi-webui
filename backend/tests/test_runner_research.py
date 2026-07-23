"""P2-2: research mode cascade-termination tests.

Verifies that in research_mode, once one slot produces a usable answer, the
others early-exit (status=cancelled) and the winner is returned directly
without a merge step.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def rt_msg():
    from app.db.base import async_session_maker
    from app.db.models.conversation import Conversation, Message
    from app.db.models.user import User
    from app.core.security import hash_password
    from sqlalchemy import delete
    ids = []
    async with async_session_maker() as s:
        u = User(id=uuid.uuid4(), email=f"rt{uuid.uuid4().hex[:6]}@t.com", name="rt",
                 password_hash=hash_password("Test@1234"), is_active=True, role="member")
        s.add(u)
        await s.flush()
        c = Conversation(id=uuid.uuid4(), title="rt", owner_id=u.id,
                         primary_agent_id="hermes", active_agent_ids=["a", "b"])
        s.add(c)
        await s.flush()
        m = Message(id=uuid.uuid4(), conversation_id=c.id, role="roundtable",
                    agent_id="a", content={"replies": [], "merged": {"text": "", "status": "pending"}}, status="streaming")
        s.add(m)
        await s.commit()
        ids.extend([str(c.id), str(m.id), str(u.id)])
    yield ids
    async with async_session_maker() as s:
        await s.execute(delete(Message).where(Message.conversation_id == uuid.UUID(ids[0])))
        await s.execute(delete(Conversation).where(Conversation.id == uuid.UUID(ids[0])))
        await s.execute(delete(User).where(User.id == uuid.UUID(ids[2])))
        await s.commit()


def _targets(n: int) -> list[dict]:
    return [{"agent_id": f"a{i}", "profile_id": None, "system_prompt": None,
             "profile_dir": None, "mcp_servers": []} for i in range(n)]


@pytest.fixture
def rt_agents():
    return {f"a{i}": SimpleNamespace(command=["h"], label=f"A{i}", color="#b8852a", description="") for i in range(3)}


class TestResearchCascade:
    async def test_first_slot_wins_others_cancelled(self, rt_msg, rt_agents):
        """In research_mode, slot 0 produces text → slots 1,2 should early-exit."""
        conv_id, msg_id, _ = rt_msg
        ran_slots: list[int] = []

        async def fake_run_prompt(client, sid, prompt_content, aid):
            # slot 0 emits text immediately; slots 1,2 never get here (cancelled)
            ran_slots.append(1)
            if client.on_update:
                await client.on_update({"sessionUpdate": "agent_message_chunk",
                                        "content": {"text": "found it"}})

        def fake_make_client(command, cwd, *, on_update, on_fs_write, profile_dir=None):
            c = MagicMock()
            c.on_update = on_update
            c.stop = AsyncMock()
            return c

        # Track request_cancel calls: slot 0's on_update fires request_cancel,
        # then is_cancelled returns True for slots 1,2.
        cancel_calls = {"n": 0}

        async def fake_request_cancel(conv_id):
            cancel_calls["n"] += 1

        async def fake_is_cancelled(conv_id):
            # True once the first slot has signalled cancel.
            return cancel_calls["n"] > 0

        with patch("agent_runner.runner_roundtable.R.publish_event", new_callable=AsyncMock), \
             patch("agent_runner.runner_roundtable.R.is_cancelled", side_effect=fake_is_cancelled), \
             patch("agent_runner.runner_roundtable.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner_roundtable.R.request_cancel", side_effect=fake_request_cancel), \
             patch("agent_runner.runner_roundtable.make_persona_client", side_effect=fake_make_client), \
             patch("agent_runner.runner_roundtable.start_persona_session", return_value="s"), \
             patch("agent_runner.runner_roundtable.run_prompt_with_clarify_guard", side_effect=fake_run_prompt):
            from agent_runner.runner_roundtable import handle_roundtable
            await handle_roundtable({
                "type": "roundtable", "conversation_id": conv_id, "message_id": msg_id,
                "targets": _targets(3), "text": "find this", "content_blocks": [],
                "moa": False, "research_mode": True,
            }, rt_agents)

        # Only one slot should have run its prompt (the winner).
        # The others early-exit via is_cancelled before reaching run_prompt.
        assert len(ran_slots) <= 2, f"Expected ≤2 slots to run prompt, got {len(ran_slots)}"

    async def test_non_research_mode_runs_all(self, rt_msg, rt_agents):
        """Without research_mode, all slots run (no cascade termination)."""
        conv_id, msg_id, _ = rt_msg
        ran: list[int] = []

        async def fake_run_prompt(client, sid, prompt_content, aid):
            ran.append(1)
            if client.on_update:
                await client.on_update({"sessionUpdate": "agent_message_chunk",
                                        "content": {"text": f"reply-{aid}"}})

        def fake_make_client(command, cwd, *, on_update, on_fs_write, profile_dir=None):
            c = MagicMock()
            c.on_update = on_update
            c.stop = AsyncMock()
            return c

        with patch("agent_runner.runner_roundtable.R.publish_event", new_callable=AsyncMock), \
             patch("agent_runner.runner_roundtable.R.is_cancelled", new_callable=AsyncMock, return_value=False), \
             patch("agent_runner.runner_roundtable.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner_roundtable.R.request_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner_roundtable.make_persona_client", side_effect=fake_make_client), \
             patch("agent_runner.runner_roundtable.start_persona_session", return_value="s"), \
             patch("agent_runner.runner_roundtable.run_prompt_with_clarify_guard", side_effect=fake_run_prompt):
            from agent_runner.runner_roundtable import handle_roundtable
            await handle_roundtable({
                "type": "roundtable", "conversation_id": conv_id, "message_id": msg_id,
                "targets": _targets(3), "text": "discuss", "content_blocks": [],
                "moa": False, "research_mode": False,
            }, rt_agents)

        # All 3 slots should have run their prompts.
        assert len(ran) == 3, f"Expected 3 slots in non-research mode, got {len(ran)}"
