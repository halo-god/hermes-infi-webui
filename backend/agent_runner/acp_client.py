"""Minimal async ACP (Agent Client Protocol) client.

Speaks JSON-RPC 2.0 over newline-delimited stdio with an agent subprocess.
We are the *client* (editor side); the agent is the server. Supports:

  client → agent (requests):  initialize, session/new, session/prompt
  client → agent (notify):    session/cancel
  agent  → client (notify):   session/update   (streaming chunks / tool calls)
  agent  → client (request):  fs/write_text_file  (produced files → workspace)

Callbacks let the runner react to streaming updates and file writes without
this module knowing anything about Redis or the DB.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("hermes.acp")

# ── Timeouts (seconds) ──
START_TIMEOUT = 30       # subprocess spawn
INIT_TIMEOUT = 30        # initialize handshake
SESSION_TIMEOUT = 30     # session/new
PROMPT_TIMEOUT = 300     # session/prompt (agent can think for a while)
CANCEL_TIMEOUT = 5       # session/cancel (fire-and-forget)

OnUpdate = Callable[[dict], Awaitable[None]]
OnFsWrite = Callable[[str, str], Awaitable[None]]  # (path, content)


class ACPError(Exception):
    pass


class ACPTimeout(ACPError):
    """Raised when an ACP call exceeds its deadline."""
    pass


class ACPClient:
    def __init__(
        self,
        command: list[str],
        cwd: str,
        *,
        protocol_version: int = 1,
        on_update: OnUpdate | None = None,
        on_fs_write: OnFsWrite | None = None,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.protocol_version = protocol_version
        self.on_update = on_update
        self.on_fs_write = on_fs_write

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_tail: deque[str] = deque(maxlen=30)
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._session_id: str | None = None
        self._closed = False

    # ── lifecycle ──
    async def start(self) -> None:
        from agent_runner import sandbox

        argv = sandbox.build_argv(self.command, self.cwd)
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=sandbox.preexec_fn(),
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        # Drain stderr so a chatty real CLI can't deadlock on a full pipe buffer.
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        logger.info("ACP subprocess started: %s (cwd=%s)", " ".join(argv), self.cwd)

    async def stop(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "ignore").rstrip()
                if text:
                    self._stderr_tail.append(text)
                    logger.debug("[agent stderr] %s", text)
        except asyncio.CancelledError:
            pass

    def _stderr_context(self) -> str:
        return " | ".join(list(self._stderr_tail)[-5:])

    # ── protocol ──
    async def initialize(self) -> dict:
        return await self._request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "clientCapabilities": {"fs": {"writeTextFile": True, "readTextFile": True}},
                "clientInfo": {"name": "hermes-runner", "version": "0.1.0"},
            },
            timeout=INIT_TIMEOUT,
        )

    async def authenticate(self, method_id: str) -> dict:
        """Optional ACP auth step for agents that advertise authMethods.

        Most local CLIs (incl. Hermes) self-authenticate at the host level
        (env API key / `hermes login`), so this is only used when configured.
        """
        return await self._request("authenticate", {"methodId": method_id}, timeout=INIT_TIMEOUT)

    async def new_session(self, cwd: str, mcp_servers: list | None = None) -> str:
        res = await self._request(
            "session/new", {"cwd": cwd, "mcpServers": mcp_servers or []}, timeout=SESSION_TIMEOUT,
        )
        self._session_id = res.get("sessionId")
        if not self._session_id:
            raise ACPError("agent did not return a sessionId")
        return self._session_id

    async def prompt(self, text: str) -> str:
        """Run one user turn. Returns the stopReason."""
        res = await self._request(
            "session/prompt",
            {
                "sessionId": self._session_id,
                "prompt": [{"type": "text", "text": text}],
            },
            timeout=PROMPT_TIMEOUT,
        )
        return res.get("stopReason", "end_turn")

    async def cancel(self) -> None:
        if self._session_id:
            await self._notify("session/cancel", {"sessionId": self._session_id})

    # ── JSON-RPC plumbing ──
    async def _request(self, method: str, params: dict, timeout: float = 60) -> dict:
        if not self._proc or self._proc.stdin is None:
            raise ACPError("subprocess not started")
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise ACPTimeout(f"{method} timed out after {timeout}s")
        return result or {}

    async def _notify(self, method: str, params: dict) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _respond(self, rid: Any, result: Any) -> None:
        await self._write({"jsonrpc": "2.0", "id": rid, "result": result})

    async def _write(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while not self._closed:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("ACP non-JSON line: %r", line[:200])
                    continue
                await self._dispatch(msg)
        except asyncio.CancelledError:
            pass
        finally:
            # Fail any in-flight requests so callers don't hang; include stderr
            # context so real-CLI startup/auth errors are diagnosable.
            tail = self._stderr_context()
            detail = "subprocess closed" + (f" — stderr: {tail}" if tail else "")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ACPError(detail))
            self._pending.clear()

    async def _dispatch(self, msg: dict) -> None:
        # Response to one of our requests.
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(ACPError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result") or {})
            return

        method = msg.get("method")
        params = msg.get("params") or {}

        # Request FROM agent (needs a response).
        if method and "id" in msg:
            if method == "fs/write_text_file":
                path = params.get("path", "untitled.txt")
                content = params.get("content", "")
                if self.on_fs_write:
                    try:
                        await self.on_fs_write(path, content)
                    except Exception:  # noqa: BLE001
                        logger.exception("on_fs_write failed")
                await self._respond(msg["id"], None)
            elif method in ("request_permission", "session_request_permission", "session/request_permission"):
                # Auto-approve edit requests for files within the workspace directory.
                # The hermes CLI sends request_permission before write_file/patch;
                # without approval the tool is blocked. We approve if the path is
                # inside cwd (the workspace).
                tool_call = params.get("toolCall") or params.get("tool_call") or {}
                logger.info("request_permission params keys=%s", list(params.keys()))
                logger.info("toolCall keys=%s", list(tool_call.keys()) if isinstance(tool_call, dict) else type(tool_call))
                # Try multiple extraction paths for the file path
                raw_input = tool_call.get("rawInput") or tool_call.get("raw_input") or {}
                args = raw_input.get("arguments") or raw_input.get("args") or {}
                edit_path = args.get("path") or args.get("file_path") or ""
                # Try toolCall.content[].path, toolCall.locations[].path
                if not edit_path:
                    for loc in (tool_call.get("locations") or []):
                        edit_path = loc.get("path") or loc.get("uri") or ""
                        if edit_path:
                            break
                if not edit_path:
                    for c in (tool_call.get("content") or []):
                        edit_path = c.get("path") or ""
                        if edit_path:
                            break
                # Try top-level params paths
                if not edit_path:
                    edit_path = params.get("path") or params.get("file_path") or ""
                # Try nested input in tool_call directly
                if not edit_path:
                    nested = tool_call.get("input") or {}
                    if isinstance(nested, dict):
                        edit_path = nested.get("path") or nested.get("file_path") or ""
                if edit_path:
                    logger.info("Extracted edit_path=%s", edit_path)
                else:
                    logger.warning("Could not extract edit_path! toolCall=%s", tool_call)
                approved = False
                if edit_path:
                    import os
                    # Resolve relative paths against the workspace cwd
                    if not os.path.isabs(edit_path):
                        edit_path = os.path.join(self.cwd, edit_path)
                    real_cwd = os.path.realpath(self.cwd)
                    real_edit = os.path.realpath(os.path.expanduser(edit_path))
                    approved = real_edit.startswith(real_cwd + os.sep) or real_edit == real_cwd
                if approved:
                    logger.info("Auto-approved edit: %s", edit_path)
                    # Also save to database via on_fs_write so workspace panel can see it.
                    # Extract content from toolCall.rawInput.arguments.content or toolCall.content
                    file_content = args.get("content") or args.get("file_content") or ""
                    if not file_content:
                        for c in (tool_call.get("content") or []):
                            if isinstance(c, dict):
                                file_content = c.get("content") or c.get("text") or ""
                            if file_content:
                                break
                    if not file_content and isinstance(tool_call.get("content"), str):
                        file_content = tool_call["content"]
                    if self.on_fs_write and edit_path and file_content:
                        try:
                            import os as _os
                            rel_path = _os.path.relpath(edit_path, self.cwd)
                            await self.on_fs_write(rel_path, file_content)
                            logger.info("Saved to workspace DB: %s (%d chars)", rel_path, len(file_content))
                        except Exception:  # noqa: BLE001
                            logger.exception("on_fs_write after approval failed")
                else:
                    logger.warning("Edit NOT approved. edit_path=%s cwd=%s", edit_path, self.cwd)
                await self._respond(msg["id"], {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": "allow_once" if approved else "deny",
                    },
                })
            else:
                # Unknown agent→client request: acknowledge with null.
                await self._respond(msg["id"], None)
            return

        # Notification FROM agent.
        if method == "session/update":
            if self.on_update:
                await self.on_update(params.get("update") or params)
