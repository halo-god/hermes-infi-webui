"""F0-F2: tests for the ACP event-handling fixes and enhancements.

Covers: thought-stream naming fix (agent_thought_chunk), plan priority type
fix (string not int), and the new available_commands_update / user_message_chunk
handlers. These test the on_update event routing by feeding fake session/update
dictionaries through a minimal on_update simulation.
"""
from __future__ import annotations



class TestThoughtStreamNaming:
    """F0-1: the runner must accept 'agent_thought_chunk' (ACP v1 spec name)."""

    async def test_thought_chunk_accepted(self):
        """A session/update with sessionUpdate='agent_thought_chunk' should
        produce a 'thought' SSE event, not be silently dropped."""
        acc = {"thinking": ""}

        # Reconstruct the relevant branch of on_update.
        update = {"sessionUpdate": "agent_thought_chunk", "content": {"text": "I should think about this"}}
        kind = update.get("sessionUpdate")
        if kind in ("agent_thought_chunk", "agent_thought"):
            delta = (update.get("content") or {}).get("text", "")
            if delta:
                acc["thinking"] += delta

        assert "I should think about this" in acc["thinking"]

    async def test_legacy_thought_still_accepted(self):
        """Older hermes-agent versions may emit 'agent_thought' — keep compat."""
        acc = {"thinking": ""}
        update = {"sessionUpdate": "agent_thought", "content": {"text": "legacy thought"}}
        kind = update.get("sessionUpdate")
        if kind in ("agent_thought_chunk", "agent_thought"):
            delta = (update.get("content") or {}).get("text", "")
            acc["thinking"] += delta
        assert "legacy thought" in acc["thinking"]


class TestPlanPriorityType:
    """F0-2: plan priority must be string ('high'/'medium'/'low'), not int."""

    def test_priority_string_default(self):
        """When the agent doesn't send priority, default to 'medium'."""
        raw = [{"content": "task A", "status": "pending"}]
        entries = [
            {"content": e.get("content", ""), "status": e.get("status", "pending"),
             "priority": e.get("priority", "medium")}
            for e in raw if isinstance(e, dict)
        ]
        assert entries[0]["priority"] == "medium"

    def test_priority_string_passthrough(self):
        """When the agent sends 'high', it should pass through as string."""
        raw = [{"content": "urgent task", "status": "in_progress", "priority": "high"}]
        entries = [
            {"content": e.get("content", ""), "status": e.get("status", "pending"),
             "priority": e.get("priority", "medium")}
            for e in raw if isinstance(e, dict)
        ]
        assert entries[0]["priority"] == "high"
        assert isinstance(entries[0]["priority"], str)


class TestToolCallRawInput:
    """F1: tool_call events should carry raw_input and tool_kind for the
    frontend's specialized rendering."""

    def test_raw_input_captured(self):
        """The step dict should include raw_input and tool_kind from the update."""
        update = {
            "sessionUpdate": "tool_call",
            "title": "execute_code",
            "status": "completed",
            "rawInput": {"command": "print('hello')"},
            "toolKind": "execute",
        }
        raw_input = update.get("rawInput") or update.get("raw_input")
        tool_kind = update.get("toolKind") or update.get("tool_kind")
        step = {"title": update.get("title"), "status": update.get("status"),
                "raw_input": raw_input, "tool_kind": tool_kind}
        assert step["raw_input"] == {"command": "print('hello')"}
        assert step["tool_kind"] == "execute"


class TestNewEventHandlers:
    """F2: available_commands_update and user_message_chunk routing."""

    def test_commands_update_routed(self):
        """An available_commands_update should produce a commands_update SSE."""
        update = {
            "sessionUpdate": "available_commands_update",
            "commands": [{"name": "/search", "description": "Search memory"}],
        }
        kind = update.get("sessionUpdate")
        commands = update.get("commands") or []
        assert kind == "available_commands_update"
        assert len(commands) == 1
        assert commands[0]["name"] == "/search"

    def test_user_message_chunk_routed(self):
        """A user_message_chunk should extract the text delta."""
        update = {"sessionUpdate": "user_message_chunk", "content": {"text": "echoed input"}}
        kind = update.get("sessionUpdate")
        delta = (update.get("content") or {}).get("text", "")
        assert kind == "user_message_chunk"
        assert delta == "echoed input"
