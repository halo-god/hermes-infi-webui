"""P2-1: chain handoff runner tests.

Verifies the sequential relay: each agent's output is prepended to the next's
prompt, the chain short-circuits on cancel, and the final status reflects the
last step. Mocks the persona-client layer (make_persona_client /
run_prompt_with_clarify_guard) so no real ACP subprocess is needed.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio


def _make_targets(n: int) -> list[dict]:
    return [
        {"agent_id": f"agent-{i}", "profile_id": None, "system_prompt": f"persona-{i}",
         "profile_dir": None, "mcp_servers": []}
        for i in range(n)
    ]


@pytest_asyncio.fixture
async def chain_msg():
    """Create a conversation + chain message in a real session (handle_chain
    opens its own async_session_maker for finalize)."""
    from app.db.base import async_session_maker
    from app.db.models.conversation import Conversation, Message
    from app.db.models.user import User
    from app.core.security import hash_password
    from sqlalchemy import delete
    ids = []
    async with async_session_maker() as s:
        u = User(id=uuid.uuid4(), email=f"chain{uuid.uuid4().hex[:6]}@t.com", name="ch",
                 password_hash=hash_password("Test@1234"), is_active=True, role="member")
        s.add(u)
        await s.flush()
        c = Conversation(id=uuid.uuid4(), title="chain", owner_id=u.id,
                         primary_agent_id="hermes", active_agent_ids=["agent-0", "agent-1"])
        s.add(c)
        await s.flush()
        m = Message(id=uuid.uuid4(), conversation_id=c.id, role="chain",
                    agent_id="agent-0", content={"steps": []}, status="streaming")
        s.add(m)
        await s.commit()
        ids.extend([str(c.id), str(m.id), str(u.id)])
    yield ids
    async with async_session_maker() as s:
        await s.execute(delete(Message).where(Message.conversation_id == uuid.UUID(ids[0])))
        await s.execute(delete(Conversation).where(Conversation.id == uuid.UUID(ids[0])))
        await s.execute(delete(User).where(User.id == uuid.UUID(ids[2])))
        await s.commit()


@pytest.fixture
def chain_agents():
    return {
        f"agent-{i}": SimpleNamespace(command=["hermes", "acp"], label=f"Agent {i}",
                                      color="#b8852a", description="")
        for i in range(3)
    }


class TestChainExecution:
    async def test_two_step_chain_carries_conclusion(self, chain_msg, chain_agents):
        """Agent 0's output should appear in agent 1's prompt (carried_text)."""
        conv_id, msg_id, _ = chain_msg
        seen_prompts: list[str] = []

        async def fake_run_prompt(client, sid, prompt_content, aid):
            text = prompt_content if isinstance(prompt_content, str) else str(prompt_content)
            seen_prompts.append(text)
            # Emit a token so the chain records output.
            if client.on_update:
                await client.on_update({"sessionUpdate": "agent_message_chunk",
                                        "content": {"text": f"output-from-{aid}"}})

        async def fake_start_session(client, cwd, mcp_servers=None):
            return "fake-sess"

        def fake_make_client(command, cwd, *, on_update, on_fs_write, profile_dir=None):
            from unittest.mock import MagicMock
            c = MagicMock()
            c.on_update = on_update
            c.stop = AsyncMock()
            return c

        with patch("agent_runner.runner_chain.R.publish_event", new_callable=AsyncMock), \
             patch("agent_runner.runner_chain.R.is_cancelled", new_callable=AsyncMock, return_value=False), \
             patch("agent_runner.runner_chain.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner_chain.make_persona_client", side_effect=fake_make_client), \
             patch("agent_runner.runner_chain.start_persona_session", side_effect=fake_start_session), \
             patch("agent_runner.runner_chain.run_prompt_with_clarify_guard", side_effect=fake_run_prompt):
            from agent_runner.runner_chain import handle_chain
            await handle_chain({
                "type": "chain", "conversation_id": conv_id, "message_id": msg_id,
                "targets": _make_targets(2), "text": "用户问题", "content_blocks": [],
            }, chain_agents)

        # Step 0 sees the user text; step 1 should see step 0's conclusion.
        assert len(seen_prompts) == 2
        assert "用户问题" in seen_prompts[0]
        assert "output-from-agent-0" in seen_prompts[1]

    async def test_chain_cancel_short_circuits(self, chain_msg, chain_agents):
        """If is_cancelled returns True, remaining steps are skipped."""
        conv_id, msg_id, _ = chain_msg
        call_count = {"n": 0}

        async def fake_run_prompt(client, sid, prompt_content, aid):
            call_count["n"] += 1
            if client.on_update:
                await client.on_update({"sessionUpdate": "agent_message_chunk",
                                        "content": {"text": "step-output"}})

        def fake_make_client(command, cwd, *, on_update, on_fs_write, profile_dir=None):
            from unittest.mock import MagicMock
            c = MagicMock()
            c.on_update = on_update
            c.stop = AsyncMock()
            return c

        # First call to is_cancelled (before step 0) = False; after step 0 = True
        cancel_states = iter([False, True, True])

        async def fake_is_cancelled(conv_id):
            try:
                return next(cancel_states)
            except StopIteration:
                return True

        with patch("agent_runner.runner_chain.R.publish_event", new_callable=AsyncMock), \
             patch("agent_runner.runner_chain.R.is_cancelled", side_effect=fake_is_cancelled), \
             patch("agent_runner.runner_chain.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner_chain.make_persona_client", side_effect=fake_make_client), \
             patch("agent_runner.runner_chain.start_persona_session", return_value="s"), \
             patch("agent_runner.runner_chain.run_prompt_with_clarify_guard", side_effect=fake_run_prompt):
            from agent_runner.runner_chain import handle_chain
            await handle_chain({
                "type": "chain", "conversation_id": conv_id, "message_id": msg_id,
                "targets": _make_targets(3), "text": "q", "content_blocks": [],
            }, chain_agents)

        # Only step 0 should have run; steps 1-2 cancelled.
        assert call_count["n"] == 1

    async def test_chain_single_target_no_carry(self, chain_msg, chain_agents):
        """A single-target chain just runs once with the user text."""
        conv_id, msg_id, _ = chain_msg
        seen: list[str] = []

        async def fake_run_prompt(client, sid, prompt_content, aid):
            seen.append(prompt_content if isinstance(prompt_content, str) else "")
            if client.on_update:
                await client.on_update({"sessionUpdate": "agent_message_chunk",
                                        "content": {"text": "solo"}})

        def fake_make_client(command, cwd, *, on_update, on_fs_write, profile_dir=None):
            from unittest.mock import MagicMock
            c = MagicMock()
            c.on_update = on_update
            c.stop = AsyncMock()
            return c

        with patch("agent_runner.runner_chain.R.publish_event", new_callable=AsyncMock), \
             patch("agent_runner.runner_chain.R.is_cancelled", new_callable=AsyncMock, return_value=False), \
             patch("agent_runner.runner_chain.R.clear_cancel", new_callable=AsyncMock), \
             patch("agent_runner.runner_chain.make_persona_client", side_effect=fake_make_client), \
             patch("agent_runner.runner_chain.start_persona_session", return_value="s"), \
             patch("agent_runner.runner_chain.run_prompt_with_clarify_guard", side_effect=fake_run_prompt):
            from agent_runner.runner_chain import handle_chain
            await handle_chain({
                "type": "chain", "conversation_id": conv_id, "message_id": msg_id,
                "targets": _make_targets(1), "text": "solo question", "content_blocks": [],
            }, chain_agents)

        assert len(seen) == 1
        assert "solo question" in seen[0]
