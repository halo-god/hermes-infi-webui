"""Task execution — runs an ACP prompt for a project task and updates its status.

Similar to runner_scheduled but tailored for executing ProjectTask items.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

from agent_runner.acp_client import ACPClient, ACPTimeout
from app.config import settings
from app.db.base import async_session_maker
from app.db.models.team import ProjectTask

logger = logging.getLogger(__name__)


async def _update_task(task_id: str, status: str, result: str | None = None) -> None:
    async with async_session_maker() as db:
        t = await db.get(ProjectTask, uuid.UUID(task_id))
        if t:
            t.status = status
            if result:
                t.description = (t.description or "") + f"\n\n--- 执行结果 ---\n{result[:2000]}"
            await db.commit()


async def handle_task_execution(task: dict, agents: dict) -> None:
    """Execute a project task: spawn an ACP agent, send the prompt, update task."""
    task_id = task["task_id"]
    agent_id = task.get("agent_id", "hermes")
    prompt_text = task["prompt"]

    await _update_task(task_id, "doing")

    agent = agents.get(agent_id) or agents.get("hermes")
    if agent is None:
        logger.error("task_execution %s: no agent available", task_id[:8])
        await _update_task(task_id, "todo")
        return

    cwd = os.path.join(settings.workspace_root, f"task-{task_id}")
    os.makedirs(cwd, exist_ok=True)

    buf = {"text": ""}

    async def on_update(update: dict) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            buf["text"] += (update.get("content") or {}).get("text", "")

    async def _noop_fs(_p: str, _c: str) -> None:
        return None

    try:
        client = ACPClient(
            agent.command, cwd,
            protocol_version=settings.acp_protocol_version,
            on_update=on_update, on_fs_write=_noop_fs,
        )
        try:
            await client.start()
            await client.initialize()
            await client.new_session(cwd)
            await client.prompt(prompt_text)
        finally:
            await client.stop()

        response = buf["text"].strip()
        logger.info("task_execution %s completed: %d chars", task_id[:8], len(response))
        await _update_task(task_id, "done", response)

    except ACPTimeout:
        logger.warning("task_execution %s timed out", task_id[:8])
        await _update_task(task_id, "todo")
    except asyncio.CancelledError:
        await _update_task(task_id, "todo")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("task_execution %s failed: %s", task_id[:8], exc)
        await _update_task(task_id, "todo")
