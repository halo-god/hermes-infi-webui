"""Shared per-persona ACP turn logic, extracted from the roundtable executor
so it can be reused by both the blocking round-table gather (runner_roundtable.py)
and persistent background subagents (runner_subagent.py) without duplicating
the clarify-auto-decline backstop or session-spawn sequence.

Split into three small pieces rather than one do-everything function so
callers can keep their own try/finally around client.stop() — a persistent
subagent needs the client to survive past the first prompt, an ephemeral
roundtable reply doesn't.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app.config import settings
from agent_runner.acp_client import ACPClient, OnFsWrite, OnUpdate, profile_env
from agent_runner.runner_clarify import deliver_clarify_response, pop_clarify_request

logger = logging.getLogger("hermes.runner")


def make_persona_client(
    command: list[str],
    cwd: str,
    *,
    on_update: OnUpdate,
    on_fs_write: OnFsWrite,
    profile_dir: str | None = None,
) -> ACPClient:
    """Construct (but don't start) an ACPClient for one persona."""
    return ACPClient(
        command, cwd, protocol_version=settings.acp_protocol_version,
        on_update=on_update, on_fs_write=on_fs_write, env=profile_env(profile_dir),
    )


async def start_persona_session(
    client: ACPClient, cwd: str, mcp_servers: list | None = None,
) -> str:
    """Start the subprocess, initialize, and create a fresh ACP session.
    Returns the session id."""
    await client.start()
    await client.initialize()
    return await client.new_session(cwd, mcp_servers=mcp_servers)


def wrap_persona_prompt(text: str, system_prompt: str | None) -> str:
    if not system_prompt:
        return text
    return f"【角色设定】\n{system_prompt}\n【角色设定结束】\n\n{text}"


async def run_prompt_with_clarify_guard(
    client: ACPClient, clarify_sid: str, prompt_text: str, label: str,
) -> None:
    """Run one prompt to completion, auto-declining any clarify request the
    agent raises mid-turn — nobody can answer an interactive confirmation
    modal for a parallel/background persona. This is a hard backstop; any
    "don't call clarify" prompt preamble asking the agent to behave is
    advisory only and can't be trusted on its own.
    """
    prompt_task = asyncio.create_task(client.prompt(prompt_text))
    while not prompt_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(prompt_task), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        try:
            data = await pop_clarify_request(clarify_sid)
            if data:
                clarify_id = data.get("clarify_id") or uuid.uuid4().hex[:12]
                logger.info("clarify auto-declined (%s): %s", label, clarify_id)
                await deliver_clarify_response(clarify_sid, clarify_id, "")
        except Exception:
            logger.debug("clarify poll failed", exc_info=True)
    await prompt_task
