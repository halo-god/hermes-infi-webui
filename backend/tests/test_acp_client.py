"""Tests for the ACP client permission-gate / prompt-params / session cleanup.

Covers:
  - request_permission outside the workspace consults on_permission_request
    (approve/deny/fail-closed); workspace edits stay auto-approved.
  - prompt() includes taskId/metadata only when provided.
  - delete_session() issues the right ACP method, best-effort.
No real subprocess is spawned — ACPClient methods are driven directly with
patched _respond/_request.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch


from agent_runner.acp_client import ACPClient, auto_deny_permission


def _mk_client(cwd: str = "/tmp/hermes-ws", on_permission_request=None) -> ACPClient:
    c = ACPClient(
        ["hermes", "acp"], cwd,
        on_permission_request=on_permission_request,
    )
    c._session_id = "sess-1"
    return c


async def _dispatch_permission(client: ACPClient, path: str, respond: AsyncMock) -> None:
    msg = {
        "jsonrpc": "2.0", "id": 1, "method": "request_permission",
        "params": {
            "toolCall": {
                "rawInput": {"arguments": {"path": path, "content": "data"}},
            },
        },
    }
    with patch.object(client, "_respond", respond):
        await client._dispatch(msg)


class TestPermissionRequest:
    async def test_workspace_outside_calls_callback_approve(self):
        client = _mk_client(on_permission_request=AsyncMock(return_value=True))
        respond = AsyncMock()
        await _dispatch_permission(client, "/tmp/outside/file.txt", respond)
        client.on_permission_request.assert_awaited_once()
        body = respond.call_args.args[1]
        assert body["outcome"]["optionId"] == "allow_once"

    async def test_workspace_outside_callback_deny(self):
        client = _mk_client(on_permission_request=AsyncMock(return_value=False))
        respond = AsyncMock()
        await _dispatch_permission(client, "/tmp/outside/file.txt", respond)
        body = respond.call_args.args[1]
        assert body["outcome"]["optionId"] == "deny"

    async def test_workspace_outside_no_callback_fail_closed(self):
        """No callback configured → deny (never silently approve outside ws)."""
        client = _mk_client(on_permission_request=None)
        respond = AsyncMock()
        await _dispatch_permission(client, "/tmp/outside/file.txt", respond)
        body = respond.call_args.args[1]
        assert body["outcome"]["optionId"] == "deny"

    async def test_callback_exception_fail_closed(self):
        client = _mk_client(on_permission_request=AsyncMock(side_effect=RuntimeError("boom")))
        respond = AsyncMock()
        await _dispatch_permission(client, "/tmp/outside/file.txt", respond)
        body = respond.call_args.args[1]
        assert body["outcome"]["optionId"] == "deny"

    async def test_workspace_inside_auto_approved_without_callback(self):
        """Edits inside cwd keep the auto-approve fast path (no callback)."""
        client = _mk_client(on_permission_request=AsyncMock())
        respond = AsyncMock()
        await _dispatch_permission(client, "/tmp/hermes-ws/docs/note.md", respond)
        client.on_permission_request.assert_not_awaited()
        body = respond.call_args.args[1]
        assert body["outcome"]["optionId"] == "allow_once"

    async def test_auto_deny_callback_denies(self):
        """The shared unattended callback is fail-closed."""
        assert await auto_deny_permission("/x", {}) is False


class TestPromptParams:
    async def test_task_id_included_when_set(self):
        client = _mk_client()
        request = AsyncMock(return_value={"stopReason": "end_turn"})
        with patch.object(client, "_request", request):
            await client.prompt("hello", task_id="task-9")
        params = request.call_args.args[1]
        assert params["taskId"] == "task-9"
        assert "metadata" not in params

    async def test_metadata_included_when_set(self):
        client = _mk_client()
        request = AsyncMock(return_value={"stopReason": "end_turn"})
        with patch.object(client, "_request", request):
            await client.prompt("hello", metadata={"source": "test"})
        params = request.call_args.args[1]
        assert params["metadata"] == {"source": "test"}
        assert "taskId" not in params

    async def test_no_optional_params_by_default(self):
        client = _mk_client()
        request = AsyncMock(return_value={"stopReason": "end_turn"})
        with patch.object(client, "_request", request):
            await client.prompt("hello")
        params = request.call_args.args[1]
        assert set(params.keys()) == {"sessionId", "prompt"}


class TestDeleteSession:
    async def test_delete_session_sends_method(self):
        client = _mk_client()
        request = AsyncMock(return_value={})
        with patch.object(client, "_request", request):
            await client.delete_session("sess-xyz")
        assert request.call_args.args[0] == "session/delete"
        assert request.call_args.args[1] == {"sessionId": "sess-xyz"}

    async def test_delete_session_no_session_noop(self):
        client = _mk_client()
        client._session_id = None
        request = AsyncMock()
        with patch.object(client, "_request", request):
            await client.delete_session()
        request.assert_not_awaited()

    async def test_delete_session_failure_swallowed(self):
        client = _mk_client()
        request = AsyncMock(side_effect=RuntimeError("agent gone"))
        with patch.object(client, "_request", request):
            await client.delete_session("sess-xyz")  # must not raise


class TestStabilityFixes:
    """Coverage for the stability review fixes."""

    async def test_on_update_exception_isolated(self):
        """C3: an exception inside on_update must not kill the read loop —
        the dispatch continues (next notification still processed)."""
        client = _mk_client()
        calls = []

        async def bad_update(update):
            calls.append(update.get("sessionUpdate"))
            raise RuntimeError("redis gone")

        client.on_update = bad_update
        with patch.object(client, "_respond", AsyncMock()):
            await client._dispatch({"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk"}}})
            # Second notification still handled despite the first raising.
            await client._dispatch({"method": "session/update", "params": {"update": {"sessionUpdate": "agent_thought_chunk"}}})
        assert calls == ["agent_message_chunk", "agent_thought_chunk"]

    def test_friendly_error_mapping(self):
        """P7: exception class names map to readable messages."""
        from agent_runner.runner import _friendly_error

        class ACPError(Exception):
            pass

        msg = _friendly_error(ACPError("subprocess closed"))
        assert "Agent 通信异常" in msg
        assert "TypeError" not in msg

        class TimeoutError2(Exception):
            pass

        assert "超时" in _friendly_error(TimeoutError2("timed out after 30s"))

        class Boom(Exception):
            pass

        assert "Boom" in _friendly_error(Boom("x"))

    def test_strip_ansi_dict_defensive(self):
        """_strip_ansi accepts structured objects without crashing."""
        from agent_runner.runner import _strip_ansi
        assert _strip_ansi({"path": "/tmp/a", "content": "x\x1b[0m"}) == '{"path": "/tmp/a", "content": "x"}'
        assert _strip_ansi(["a", "b"]) == '["a", "b"]'
        assert _strip_ansi(None) == ""
