"""Scheduled task execution — runs a single ACP prompt for a cron-triggered task.

Each task has a dedicated conversation (type="scheduled") where results are
persisted as messages. After execution, a user notification is published so
the user sees a toast + unread badge.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

from agent_runner.acp_client import ACPClient, ACPTimeout
from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.conversation import Conversation, Message
from app.db.models.scheduled import ScheduledTask

logger = logging.getLogger(__name__)


async def _update_status(task_id: str, status: str) -> None:
    async with async_session_maker() as db:
        t = await db.get(ScheduledTask, uuid.UUID(task_id))
        if t:
            t.last_status = status
            if status == "success":
                t.success_count = (t.success_count or 0) + 1
            elif status == "failed":
                t.fail_count = (t.fail_count or 0) + 1
            await db.commit()


async def _get_or_create_conversation(task: ScheduledTask, user_id: str) -> uuid.UUID:
    """Get the task's dedicated conversation, creating one on first run."""
    if task.conversation_id is not None:
        return task.conversation_id
    conv = Conversation(
        id=uuid.uuid4(),
        title=f"⏰ {task.name}",
        owner_id=uuid.UUID(user_id),
        primary_agent_id=task.agent_id,
        active_agent_ids=[task.agent_id],
        type="scheduled",
    )
    task.conversation_id = conv.id
    return conv.id


async def _save_result(
    conversation_id: uuid.UUID, agent_id: str, text: str, task_id: str,
) -> None:
    """Persist the agent's response as a message in the task's conversation."""
    async with async_session_maker() as db:
        msg = Message(
            conversation_id=conversation_id,
            role="agent",
            agent_id=agent_id,
            content={"text": text, "scheduled_task_id": task_id},
            status="complete",
        )
        db.add(msg)
        # Touch updated_at so the conversation surfaces in recency sorts.
        conv = await db.get(Conversation, conversation_id)
        if conv:
            from datetime import datetime, timezone
            conv.updated_at = datetime.now(tz=timezone.utc)
        await db.commit()


async def _notify_user(user_id: str, conversation_id: uuid.UUID, title: str, snippet: str) -> None:
    """Send a cross-conversation notification so the user sees a toast + badge."""
    try:
        await R.publish_user_event(user_id, {
            "type": "notify",
            "conversation_id": str(conversation_id),
            "title": title,
            "snippet": snippet[:100],
            "mention": False,
        })
    except Exception:  # noqa: BLE001
        logger.debug("notify_user failed for scheduled task", exc_info=True)


async def handle_scheduled(task: dict, agents: dict) -> None:
    """Execute a scheduled task: spawn an ACP agent, send the prompt, persist
    the result into a dedicated conversation, and notify the user."""
    task_id = task["scheduled_task_id"]
    agent_id = task.get("agent_id", "hermes")
    prompt_text = task["prompt"]
    user_id = task.get("user_id", "")

    await _update_status(task_id, "running")

    # Resolve or create the task's dedicated conversation.
    async with async_session_maker() as db:
        t = await db.get(ScheduledTask, uuid.UUID(task_id))
        if t is None:
            logger.error("scheduled task %s not found", task_id[:8])
            return
        conv_id = await _get_or_create_conversation(t, user_id)
        task_name = t.name
        await db.commit()

    agent = agents.get(agent_id) or agents.get("hermes")
    if agent is None:
        logger.error("scheduled task %s: no agent available", task_id[:8])
        await _update_status(task_id, "failed")
        await _notify_user(user_id, conv_id, f"⏰ {task_name} 执行失败", "没有可用的 Agent")
        return

    cwd = os.path.join(settings.workspace_root, f"sched-{task_id}")
    os.makedirs(cwd, exist_ok=True)

    buf = {"text": "", "steps": []}

    async def on_update(update: dict) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            buf["text"] += (update.get("content") or {}).get("text", "")
        elif kind == "tool_call":
            buf["steps"].append({"title": update.get("title"), "status": update.get("status")})

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
            task_id[:8], len(response), len(buf["steps"]),
        )

        # Persist the result + notify.
        if response:
            content = response
            if buf["steps"]:
                content += "\n\n---\n"
            await _save_result(conv_id, agent_id, content, task_id)
            await _notify_user(user_id, conv_id, f"⏰ {task_name} 已完成", response[:100])
        else:
            await _notify_user(user_id, conv_id, f"⏰ {task_name} 已执行", "Agent 未返回内容")

        await _update_status(task_id, "success")

    except ACPTimeout:
        logger.warning("scheduled task %s timed out", task_id[:8])
        await _save_result(conv_id, agent_id, "（执行超时）", task_id)
        await _notify_user(user_id, conv_id, f"⏰ {task_name} 超时", "任务执行超时")
        await _update_status(task_id, "failed")
    except asyncio.CancelledError:
        await _update_status(task_id, "failed")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("scheduled task %s failed: %s", task_id[:8], exc)
        await _save_result(conv_id, agent_id, f"（执行失败：{exc.__class__.__name__}）", task_id)
        await _notify_user(user_id, conv_id, f"⏰ {task_name} 执行失败", str(exc)[:100])
        await _update_status(task_id, "failed")
