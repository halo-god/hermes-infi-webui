"""Scheduled task execution — runs a single ACP prompt for a cron-triggered task.

Unlike `handle_single`, there is no conversation or chat stream — the agent's
response is logged and the task's `last_status` is updated to success/failed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

from agent_runner.acp_client import ACPClient, ACPTimeout
from app.config import settings
from app.db.base import async_session_maker
from app.db.models.scheduled import ScheduledTask

logger = logging.getLogger(__name__)


async def _update_status(task_id: str, status: str) -> None:
    async with async_session_maker() as db:
        t = await db.get(ScheduledTask, uuid.UUID(task_id))
        if t:
            t.last_status = status
            await db.commit()


async def handle_scheduled(task: dict, agents: dict) -> None:
    """Execute a scheduled task: spawn an ACP agent, send the prompt, log result."""
    task_id = task["scheduled_task_id"]
    agent_id = task.get("agent_id", "hermes")
    prompt_text = task["prompt"]

    await _update_status(task_id, "running")

    agent = agents.get(agent_id) or agents.get("hermes")
    if agent is None:
        logger.error("scheduled task %s: no agent available", task_id[:8])
        await _update_status(task_id, "failed")
        return

    cwd = os.path.join(settings.workspace_root, f"sched-{task_id}")
    os.makedirs(cwd, exist_ok=True)

    buf = {"text": "", "files": []}

    async def on_update(update: dict) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            buf["text"] += (update.get("content") or {}).get("text", "")
        elif kind == "tool_call":
            step = {"title": update.get("title"), "status": update.get("status")}
            buf["files"].append(step)
            logger.info("scheduled task %s: tool_call %s (%s)", task_id[:8], step["title"], step["status"])

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
        logger.info(
            "scheduled task %s completed: %d chars response, %d tool calls",
            task_id[:8], len(response), len(buf["files"]),
        )
        await _update_status(task_id, "success")

    except ACPTimeout:
        logger.warning("scheduled task %s timed out", task_id[:8])
        await _update_status(task_id, "failed")
    except asyncio.CancelledError:
        await _update_status(task_id, "failed")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("scheduled task %s failed: %s", task_id[:8], exc)
        await _update_status(task_id, "failed")
