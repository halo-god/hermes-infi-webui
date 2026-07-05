"""Persistent, non-blocking ACP subagent sessions spawned by a parent
conversation. Unlike handle_roundtable's synchronous parallel-then-merge, a
subagent's session survives across multiple `subagent_send` turns and runs
detached from the runner's semaphore-gated task slots (see runner.py's
dispatch for subagent_spawn/subagent_send — both are fired as background
tasks, not awaited inline, so a long-running subagent never occupies a
MAX_CONCURRENT slot for its whole lifetime).

The subagent's transcript is just normal Message rows on its own headless
Conversation (type="subagent"), so the existing chat UI/streaming renders it
with zero changes — this module only owns orchestration state transitions.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.conversation import Conversation, Message
from app.db.models.subagent import BackgroundSubagent
from agent_runner import storage
from agent_runner.acp_client import ACPClient, ACPTimeout
from agent_runner.acp_persona import (
    make_persona_client,
    run_prompt_with_clarify_guard,
    start_persona_session,
    wrap_persona_prompt,
)
from agent_runner.subagent_pool import SubagentPool

logger = logging.getLogger("hermes.runner")

# DB rows in these statuses claim a live subprocess exists somewhere.
_LIVE_STATUSES = ("starting", "running", "idle", "waiting_input")


async def _set_status(
    subagent_id: str, status: str, *, error_detail: str | None = None, touch_active: bool = True,
) -> None:
    async with async_session_maker() as db:
        row = await db.get(BackgroundSubagent, uuid.UUID(subagent_id))
        if row is None:
            return
        row.status = status
        if touch_active:
            row.last_active_at = datetime.now(tz=timezone.utc)
        if error_detail is not None:
            row.error_detail = error_detail
        await db.commit()


async def _create_subagent_message(conversation_id: uuid.UUID, agent_id: str) -> str:
    async with async_session_maker() as db:
        msg = Message(
            conversation_id=conversation_id, role="agent", agent_id=agent_id,
            content={"text": ""}, status="streaming",
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return str(msg.id)


async def _finalize_message(message_id: str, text: str, status: str) -> None:
    async with async_session_maker() as db:
        msg = await db.get(Message, uuid.UUID(message_id))
        if msg:
            msg.content = {"text": text}
            msg.status = status
            convo = await db.get(Conversation, msg.conversation_id)
            if convo:
                convo.updated_at = datetime.now(tz=timezone.utc)
            await db.commit()


async def _nudge(subagent_id: str, parent_conversation_id: str, owner_id: str, status: str) -> None:
    """Notify the parent conversation (if the user is looking at it) and the
    user's cross-conversation notify stream (if they've navigated away)."""
    payload = {"type": "subagent_nudge", "subagent_id": subagent_id, "status": status}
    try:
        await R.publish_event(parent_conversation_id, payload)
    except Exception:
        logger.debug("subagent nudge to parent conv failed", exc_info=True)
    try:
        await R.publish_user_event(owner_id, payload)
    except Exception:
        logger.debug("subagent nudge to user stream failed", exc_info=True)


async def _run_turn(
    subagent_id: str,
    client: ACPClient,
    clarify_sid: str,
    subconv_id: str,
    cwd: str,
    agent_id: str,
    prompt_text: str,
    pool: SubagentPool,
    parent_conversation_id: str,
    owner_id: str,
) -> None:
    """Run one turn on an already-spawned persistent client: write a fresh
    Message row, stream into evt:conv:{subconv_id} exactly like a normal chat
    turn, then update orchestration status and nudge the parent."""
    message_id = await _create_subagent_message(uuid.UUID(subconv_id), agent_id)
    acc = {"text": ""}

    async def on_update(update: dict) -> None:
        if update.get("sessionUpdate") == "agent_message_chunk":
            delta = (update.get("content") or {}).get("text", "")
            if delta:
                acc["text"] += delta
                await R.publish_event(subconv_id, {"type": "token", "message_id": message_id, "delta": delta})
        pool.touch(subagent_id)

    async def on_fs_write(path: str, content: str) -> None:
        f = await storage.save_file(uuid.UUID(subconv_id), path, content, agent_id, uuid.UUID(message_id))
        from app.core.files import confine_to_dir, safe_relative_path
        disk_path = confine_to_dir(cwd, safe_relative_path(path))
        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        with open(disk_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        await R.publish_event(subconv_id, {
            "type": "file", "message_id": message_id, "file_id": str(f.id),
            "name": f.name, "kind": f.kind, "version": f.current_version,
        })

    # Rebind per-turn callbacks on the persistent client — same pattern
    # SessionPool.get() uses when reusing a live client across chat turns.
    client.on_update = on_update
    client.on_fs_write = on_fs_write

    try:
        await run_prompt_with_clarify_guard(client, clarify_sid, prompt_text, subagent_id)
        msg_status = "complete"
        subagent_status = "idle"  # turn finished cleanly, waiting for next input
        error_detail = None
    except ACPTimeout as exc:
        logger.error("subagent %s prompt timed out: %s", subagent_id[:8], exc)
        msg_status = "error"
        subagent_status = "timeout"
        acc["text"] = acc["text"] or "（响应超时）"
        error_detail = acc["text"]
    except Exception as exc:  # noqa: BLE001
        logger.exception("subagent %s prompt failed", subagent_id[:8])
        msg_status = "error"
        subagent_status = "error"
        acc["text"] = acc["text"] or f"（执行失败：{exc.__class__.__name__}）"
        error_detail = acc["text"]

    await _finalize_message(message_id, acc["text"], msg_status)
    await R.publish_event(subconv_id, {"type": "done", "message_id": message_id, "status": msg_status})
    await _set_status(subagent_id, subagent_status, error_detail=error_detail)

    if subagent_status in ("error", "timeout"):
        await pool.drop(subagent_id)
    # Nudge on every turn completion, not just failure — a subagent's whole
    # point is "go do this without blocking me," so "idle" (turn done,
    # session still alive for follow-ups) is the success signal the parent
    # is waiting for, not just error/timeout.
    await _nudge(subagent_id, parent_conversation_id, owner_id, subagent_status)


async def handle_subagent_spawn(task: dict, agents: dict, pool: SubagentPool) -> None:
    """Fired as a detached background task by runner.py — does not occupy a
    MAX_CONCURRENT slot for the subagent's whole lifetime."""
    subagent_id = task["subagent_id"]
    agent_id = task.get("agent_id", "hermes")
    system_prompt = task.get("system_prompt")
    profile_dir = task.get("profile_dir")
    mcp_servers = task.get("mcp_servers") or []
    initial_prompt = task.get("initial_prompt") or ""

    async with async_session_maker() as db:
        row = await db.get(BackgroundSubagent, uuid.UUID(subagent_id))
        if row is None:
            return
        subconv_id = str(row.subagent_conversation_id)
        parent_conversation_id = str(row.parent_conversation_id)
        owner_id = str(row.owner_id)

    agent = agents.get(agent_id) or agents.get("hermes")
    if agent is None:
        await _set_status(subagent_id, "error", error_detail="没有可用的 agent")
        await _nudge(subagent_id, parent_conversation_id, owner_id, "error")
        return

    cwd = os.path.join(settings.workspace_root, subconv_id)
    os.makedirs(cwd, exist_ok=True)

    async def _noop_update(_u: dict) -> None:
        return None

    async def _noop_fs(_p: str, _c: str) -> None:
        return None

    client = make_persona_client(
        agent.command, cwd, on_update=_noop_update, on_fs_write=_noop_fs, profile_dir=profile_dir,
    )
    try:
        session_id = await start_persona_session(client, cwd, mcp_servers)
    except Exception as exc:  # noqa: BLE001
        logger.exception("subagent %s failed to start", subagent_id[:8])
        await client.stop()
        await _set_status(subagent_id, "error", error_detail=f"启动失败：{exc.__class__.__name__}")
        await _nudge(subagent_id, parent_conversation_id, owner_id, "error")
        return

    pool.register(
        subagent_id, client,
        idle_timeout=settings.subagent_idle_timeout_seconds,
        max_lifetime=settings.subagent_max_lifetime_seconds,
    )
    await _set_status(subagent_id, "running")

    clarify_sid = session_id or subconv_id
    prompt_text = wrap_persona_prompt(initial_prompt, system_prompt)
    await _run_turn(
        subagent_id, client, clarify_sid, subconv_id, cwd, agent_id, prompt_text,
        pool, parent_conversation_id, owner_id,
    )


async def handle_subagent_send(task: dict, agents: dict, pool: SubagentPool) -> None:
    """Fired as a detached background task by runner.py, same as spawn."""
    subagent_id = task["subagent_id"]
    text = task.get("text", "")

    client = pool.get(subagent_id)
    async with async_session_maker() as db:
        row = await db.get(BackgroundSubagent, uuid.UUID(subagent_id))
        if row is None:
            return
        subconv_id = str(row.subagent_conversation_id)
        parent_conversation_id = str(row.parent_conversation_id)
        owner_id = str(row.owner_id)
        agent_id = row.agent_id

    if client is None:
        # Subprocess is gone (evicted/crashed/runner restarted) — surface
        # this clearly instead of silently dropping the follow-up message.
        await _set_status(subagent_id, "error", error_detail="子代理会话已结束，无法继续对话")
        await _nudge(subagent_id, parent_conversation_id, owner_id, "error")
        return

    await _set_status(subagent_id, "running")
    cwd = os.path.join(settings.workspace_root, subconv_id)
    clarify_sid = client._session_id or subconv_id
    await _run_turn(
        subagent_id, client, clarify_sid, subconv_id, cwd, agent_id, text,
        pool, parent_conversation_id, owner_id,
    )


async def sweep_expired_subagents(pool: SubagentPool) -> None:
    """Called from the runner's heartbeat loop. Drop subagents past their
    idle/max-lifetime deadline and finalize their DB status + nudge."""
    for subagent_id, reason in await pool.evict_expired():
        async with async_session_maker() as db:
            row = await db.get(BackgroundSubagent, uuid.UUID(subagent_id))
            if row is None or row.status not in _LIVE_STATUSES:
                continue
            row.status = "timeout" if reason == "max_lifetime" else "stopped"
            row.error_detail = "超过最长运行时间被回收" if reason == "max_lifetime" else "空闲超时被回收"
            parent_conversation_id = str(row.parent_conversation_id)
            owner_id = str(row.owner_id)
            new_status = row.status
            await db.commit()
        await _nudge(subagent_id, parent_conversation_id, owner_id, new_status)


async def stop_subagent(subagent_id: str, pool: SubagentPool) -> None:
    """Handle the acp:control 'subagent_stop' message."""
    await pool.drop(subagent_id)
    async with async_session_maker() as db:
        row = await db.get(BackgroundSubagent, uuid.UUID(subagent_id))
        if row is None:
            return
        row.status = "stopped"
        parent_conversation_id = str(row.parent_conversation_id)
        owner_id = str(row.owner_id)
        await db.commit()
    await _nudge(subagent_id, parent_conversation_id, owner_id, "stopped")


async def reconcile_background_subagents() -> None:
    """Called once at runner startup. The in-memory SubagentPool always
    starts empty, so any DB row still claiming to be live from a previous
    process is orphaned — mark it `interrupted` rather than leaving a stale
    status that will never update again.

    This is a conscious v1 limitation: metadata survives a restart, the
    actual subprocess does not. Resurrecting the process would need the
    underlying agent CLI's own cross-restart session-resume guarantees,
    which is out of scope here.
    """
    async with async_session_maker() as db:
        result = await db.execute(
            select(BackgroundSubagent).where(BackgroundSubagent.status.in_(_LIVE_STATUSES))
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = "interrupted"
            row.error_detail = "runner 重启，后台任务未能恢复"
        if rows:
            await db.commit()
    if rows:
        logger.info("Marked %d background subagent(s) as interrupted after restart", len(rows))
