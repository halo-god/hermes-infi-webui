"""Scheduled task execution — runs a single ACP prompt for a cron-triggered task.

Each task has a dedicated conversation (type="scheduled") where results are
persisted as messages. After execution, a user notification is published so
the user sees a toast + unread badge.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid

from agent_runner.acp_client import ACPClient, ACPTimeout
from agent_runner.acp_client import auto_deny_permission
from agent_runner import storage
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


async def _get_or_create_conversation(db, task: ScheduledTask, user_id: str) -> uuid.UUID:
    """Get the task's dedicated conversation, creating one on first run."""
    if task.conversation_id is not None:
        conv = await db.get(Conversation, task.conversation_id)
        if conv is not None and task.profile_id is not None:
            # 存量回填：profile_id 与 active_profile_ids 一并补齐，保证前端
            # 按 profile 渲染的助手身份正确（历史会话创建时未存）。
            if conv.profile_id is None:
                conv.profile_id = str(task.profile_id)
            # active_profile_ids 是 JSONB 字符串数组 —— 必须 str()，
            # 否则 UUID 对象进 asyncpg jsonb codec 会 TypeError。
            if str(task.profile_id) not in (conv.active_profile_ids or []):
                conv.active_profile_ids = [*(conv.active_profile_ids or []), str(task.profile_id)]
        return task.conversation_id
    conv = Conversation(
        id=uuid.uuid4(),
        title=f"⏰ {task.name}",
        owner_id=uuid.UUID(user_id),
        primary_agent_id=task.agent_id,
        active_agent_ids=[task.agent_id],
        active_profile_ids=[str(task.profile_id)] if task.profile_id else [],
        profile_id=str(task.profile_id) if task.profile_id else None,
        type="scheduled",
    )
    db.add(conv)
    task.conversation_id = conv.id
    return conv.id


async def _save_result(
    conversation_id: uuid.UUID, agent_id: str, text: str, task_id: str,
    profile_id: str | None = None,
) -> None:
    """Persist the agent's response as a message in the task's conversation."""
    async with async_session_maker() as db:
        msg = Message(
            conversation_id=conversation_id,
            role="agent",
            agent_id=agent_id,
            profile_id=uuid.UUID(profile_id) if profile_id else None,
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


async def _sync_workspace_files(cwd: str, conv_id: uuid.UUID, agent_id: str) -> None:
    """Register files the agent produced during this scheduled run into the
    conversation workspace (DB + MinIO) so the workspace panel lists them —
    scheduled runs have no workspace_watcher, so without this the files stay
    invisible even though they live in the conversation's workspace dir."""
    from app.core.files import safe_relative_path

    for dirpath, dirnames, filenames in os.walk(cwd):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") or fn.endswith((".tmp", ".hermes")):
                continue
            full = os.path.join(dirpath, fn)
            rel = safe_relative_path(os.path.relpath(full, cwd))
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except Exception:
                continue
            try:
                await storage.save_file(conv_id, rel, content, agent_id, None)
                logger.info("Scheduled run synced workspace file: %s", rel)
            except Exception:
                logger.exception("Failed to sync scheduled workspace file: %s", rel)


_MEDIA_RE = re.compile(r"MEDIA:(\S+)")
_MEDIA_MAX_BYTES = 5 * 1024 * 1024


async def _sync_media_files(response: str, cwd: str, conv_id: uuid.UUID, agent_id: str) -> None:
    """Pull files the agent pointed at with ``MEDIA:/abs/path`` into the
    conversation workspace. Scheduled agents often run external scripts whose
    output lands outside cwd (e.g. ~/Downloads), so those files never show up
    in the workspace panel without this. Text-like files only; anything bigger
    than _MEDIA_MAX_BYTES or not decodable as UTF-8 is skipped."""
    for m in _MEDIA_RE.finditer(response or ""):
        raw = m.group(1).strip().strip("`'\"")
        if not raw or raw.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip")):
            continue
        path = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
        if not await asyncio.to_thread(os.path.isfile, path):
            continue
        if await asyncio.to_thread(os.path.getsize, path) > _MEDIA_MAX_BYTES:
            logger.info("Skipping oversized MEDIA file: %s", path)
            continue
        rel = os.path.basename(path)
        try:
            with open(path, "r", encoding="utf-8", errors="strict") as fh:
                content = fh.read()
        except (UnicodeDecodeError, OSError):
            logger.info("Skipping non-text MEDIA file: %s", path)
            continue
        try:
            await storage.save_file(conv_id, rel, content, agent_id, None)
            logger.info("Scheduled run synced MEDIA file: %s", rel)
        except Exception:
            logger.exception("Failed to sync MEDIA file: %s", path)


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
    profile_id = task.get("profile_id")
    prompt_text = task["prompt"]
    user_id = task.get("user_id", "")

    # S1: per-task run lock — a long task (up to acp_prompt_timeout) overlapping
    # its own cron tick would otherwise double-run concurrently. If another
    # execution holds the lock, skip silently (the running one reports back).
    lock_key = f"acp:sched:{task_id}"
    try:
        locked = await R.get_redis().set(lock_key, "1", nx=True, ex=settings.acp_prompt_timeout + 60)
    except Exception:  # noqa: BLE001 — Redis hiccup must not block execution
        locked = True
    if not locked:
        logger.info("scheduled task %s already running — skipping overlapping tick", task_id[:8])
        return

    await _update_status(task_id, "running")

    # Resolve or create the task's dedicated conversation + load profile.
    async with async_session_maker() as db:
        t = await db.get(ScheduledTask, uuid.UUID(task_id))
        if t is None:
            logger.error("scheduled task %s not found", task_id[:8])
            return
        conv_id = await _get_or_create_conversation(db, t, user_id)
        task_name = t.name
        # Load the selected profile for persona + HERMES_HOME.
        profile_dir = None
        system_prompt = None
        if profile_id:
            from app.db.models.agent import Profile
            profile = await db.get(Profile, uuid.UUID(profile_id))
            if profile:
                from app.services.conversation_service import _profile_dir
                profile_dir = _profile_dir(profile)
                system_prompt = profile.system_prompt or None
                agent_id = profile.default_agent_id or agent_id
                logger.info("scheduled task %s: using profile %s (dir=%s)", task_id[:8], profile.name, profile_dir)
        await db.commit()

    agent = agents.get(agent_id) or agents.get("hermes")
    if agent is None:
        logger.error("scheduled task %s: no agent available", task_id[:8])
        await _update_status(task_id, "failed")
        await _notify_user(user_id, conv_id, f"⏰ {task_name} 执行失败", "没有可用的 Agent")
        return

    # Work in the conversation's own workspace dir (same layout as normal
    # chats) so files the agent produces land where the workspace panel
    # expects them — not in a separate sched-{task_id} dir.
    cwd = os.path.join(settings.workspace_root, str(conv_id))
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
        from agent_runner.acp_client import profile_env
        client = ACPClient(
            agent.command, cwd,
            protocol_version=settings.acp_protocol_version,
            on_update=on_update, on_fs_write=_noop_fs,
            env=profile_env(profile_dir),
            on_permission_request=auto_deny_permission,
        )
        try:
            await client.start()
            await client.initialize()
            # Bug 9: apply auth if configured (same as handle_single cold path).
            if settings.hermes_acp_auth_method:
                await client.authenticate(settings.hermes_acp_auth_method)
            session_id = await client.new_session(cwd)
            # Bug 8: set dont_ask mode so unattended cron tasks don't hang.
            if session_id:
                try:
                    await client.set_session_mode(session_id, "dont_ask")
                except Exception:
                    logger.debug("set_session_mode failed for scheduled task", exc_info=True)
            # Inject system_prompt as persona prefix (same pattern as handle_single).
            effective_prompt = prompt_text
            if system_prompt:
                effective_prompt = f"【角色设定】\n{system_prompt}\n【角色设定结束】\n\n{prompt_text}"
            await client.prompt(effective_prompt)
        finally:
            await client.stop()

        response = buf["text"].strip()
        logger.info(
            "scheduled task %s completed: %d chars response, %d tool calls",
            task_id[:8], len(response), len(buf["steps"]),
        )

        # Register files the agent produced into the conversation workspace
        # (files written via execute_code/terminal land on disk in cwd without
        # any DB row — sync them so the workspace panel shows them).
        try:
            await _sync_workspace_files(cwd, conv_id, agent_id)
            await _sync_media_files(response, cwd, conv_id, agent_id)
        except Exception:
            logger.exception("scheduled workspace sync failed for %s", task_id[:8])

        # Persist the result + notify.
        if response:
            content = response
            if buf["steps"]:
                content += "\n\n---\n"
            await _save_result(conv_id, agent_id, content, task_id, profile_id)
            await _notify_user(user_id, conv_id, f"⏰ {task_name} 已完成", response[:100])
            await _update_status(task_id, "success")
        else:
            # P3: an empty response is a failure (agent produced nothing), not
            # a success — don't inflate success_count with no-op runs.
            await _notify_user(user_id, conv_id, f"⏰ {task_name} 未返回内容", "Agent 未返回任何内容")
            await _update_status(task_id, "failed")

    except ACPTimeout:
        logger.warning("scheduled task %s timed out", task_id[:8])
        await _save_result(conv_id, agent_id, "（执行超时）", task_id, profile_id)
        await _notify_user(user_id, conv_id, f"⏰ {task_name} 超时", "任务执行超时")
        await _update_status(task_id, "failed")
    except asyncio.CancelledError:
        await _update_status(task_id, "failed")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("scheduled task %s failed: %s", task_id[:8], exc)
        await _save_result(conv_id, agent_id, f"（执行失败：{exc.__class__.__name__}）", task_id, profile_id)
        await _notify_user(user_id, conv_id, f"⏰ {task_name} 执行失败", str(exc)[:100])
        await _update_status(task_id, "failed")
    finally:
        # Release the run lock so the next tick can fire (best-effort).
        try:
            await R.get_redis().delete(lock_key)
        except Exception:  # noqa: BLE001
            pass
