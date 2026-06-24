"""Agent Runner main loop — the ACP gateway.

  Redis Stream `acp:prompt`  ──►  ACP subprocess (Hermes/mock)  ──►  Redis Stream `evt:conv:{id}`

Performance: the per-token path is Redis-only (publish), never the DB. The
agent message row is written once on completion.

Stability features:
  - Singleton lock: only one runner active at a time (Redis distributed lock)
  - ACP timeouts: prompt 600s, start/init 30s — no infinite hangs
  - Stale reclaim: stuck pending messages auto-claimed after 60s
  - Graceful shutdown: SIGTERM/SIGINT handled, ACP subprocesses cleaned up
  - Concurrency: up to MAX_CONCURRENT tasks processed in parallel
  - Dead letter queue: failed tasks after MAX_RETRIES sent to DLQ
  - Prometheus metrics: task counts, durations, session pool, Redis ops
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
import uuid
from datetime import datetime, timezone

from redis.exceptions import ResponseError

from app.config import settings
from app.core.logging import configure_logging
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.agent import Agent
from app.db.models.conversation import Conversation, Message
from sqlalchemy import select
from agent_runner import discovery, storage
from agent_runner.acp_client import ACPTimeout
from agent_runner.session_pool import SessionPool
from agent_runner.metrics import (
    TASKS_ENQUEUED,
    TASKS_COMPLETED,
    TASKS_FAILED,
    TASK_DURATION,
    DLQ_MESSAGES,
)

logger = logging.getLogger("hermes.runner")


def _write_text(path: str, content: str) -> None:
    """Synchronous file write for use with asyncio.to_thread."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


# ── Stability constants ──
LOCK_KEY = "hermes:runner:lock"
LOCK_TTL = 30          # seconds; must be > heartbeat interval
HEARTBEAT_INTERVAL = 10  # refresh lock every N seconds
STALE_THRESHOLD_MS = 120_000  # 2 minutes — reclaim stuck pending messages
RECLAIM_INTERVAL = 30   # check for stale messages every N seconds
MAX_CONCURRENT = 5      # max tasks processed in parallel
MAX_RETRIES = 3         # max retries before sending to DLQ
DLQ_STREAM = "acp:dlq"  # dead letter queue stream


class Runner:
    def __init__(self) -> None:
        self.pool = SessionPool()
        self.agents: dict[str, discovery.DiscoveredAgent] = {}
        self._shutdown = False
        self._lock_token: str | None = None
        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        self._active_tasks: set[asyncio.Task] = set()
        self._bg_tasks: set[asyncio.Task] = set()
        self._task_retries: dict[str, int] = {}  # entry_id -> retry count

    # ── Singleton lock ──
    async def _acquire_lock(self) -> bool:
        """Try to acquire a distributed lock. Returns True if we are the leader."""
        self._lock_token = str(uuid.uuid4())
        redis = R.get_redis()
        try:
            ok = await redis.set(LOCK_KEY, self._lock_token, nx=True, ex=LOCK_TTL)
            if ok:
                logger.info("Runner lock acquired (token=%s)", self._lock_token[:8])
                return True
            existing = await redis.get(LOCK_KEY)
            if existing:
                logger.warning("Another runner is active (token=%s). Exiting.", existing[:8])
            return False
        except Exception:
            logger.exception("Failed to acquire runner lock")
            return False

    async def _refresh_lock(self) -> None:
        """Refresh the lock TTL to prevent expiry while we're alive."""
        if not self._lock_token:
            return
        redis = R.get_redis()
        try:
            current = await redis.get(LOCK_KEY)
            if current and str(current) == self._lock_token:
                await redis.expire(LOCK_KEY, LOCK_TTL)
        except Exception:
            logger.warning("Failed to refresh runner lock")

    async def _release_lock(self) -> None:
        """Release the lock only if we own it (Lua-safe compare-and-delete)."""
        if not self._lock_token:
            return
        redis = R.get_redis()
        try:
            lua = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            await redis.eval(lua, 1, LOCK_KEY, self._lock_token)
            logger.info("Runner lock released")
        except Exception:
            logger.warning("Failed to release runner lock (non-fatal)")

    # ── startup ──
    async def register_agents(self) -> None:
        found = await discovery.scan()
        self.agents = {a.id: a for a in found}
        if not found:
            return
        async with async_session_maker() as db:
            # Batch-load existing agents in one query.
            existing = {
                a.id: a
                for a in (
                    await db.execute(
                        select(Agent).where(
                            Agent.id.in_([a.id for a in found])
                        )
                    )
                ).scalars().all()
            }
            for a in found:
                row = existing.get(a.id)
                if row is None:
                    row = Agent(id=a.id)
                    db.add(row)
                row.label = a.label
                row.kind = a.kind
                row.available = a.available
                row.official = a.official
                row.version = a.version
                row.color = a.color
                row.icon = a.icon
                row.description = a.description
                row.command = a.command
                row.last_seen_at = datetime.now(tz=timezone.utc)
            await db.commit()
        logger.info("Registered %d agent(s): %s", len(found), ", ".join(self.agents))

    async def ensure_group(self) -> None:
        try:
            await R.get_redis().xgroup_create(
                settings.acp_stream, settings.acp_group, id="0", mkstream=True
            )
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        # Create DLQ stream if it doesn't exist
        try:
            await R.get_redis().xadd(DLQ_STREAM, {"_init": "1"}, maxlen=1000)
        except Exception:
            pass

    # ── Signal handling ──
    def _handle_signal(self, sig: int) -> None:
        logger.info("Received signal %s, shutting down gracefully...", sig)
        self._shutdown = True

    # ── Stale message reclaim ──
    async def _reclaim_stale(self) -> None:
        """Claim stuck pending messages that exceed STALE_THRESHOLD_MS."""
        try:
            redis = R.get_redis()
            result = await redis.xautoclaim(
                settings.acp_stream, settings.acp_group,
                settings.acp_consumer, STALE_THRESHOLD_MS, "0-0",
            )
            if result and len(result) > 1:
                claimed = result[1]
                for msg_id, fields in claimed:
                    logger.warning("Reclaimed stale message %s", msg_id)
                    data = fields.get(b"data", fields.get("data"))
                    if data:
                        # Re-enqueue FIRST (so message is not lost if we crash
                        # between re-enqueue and ACK), then ACK the original.
                        await redis.xadd(settings.acp_stream, {"data": data})
                        logger.info("Re-enqueued stale message %s", msg_id)
                    await redis.xack(settings.acp_stream, settings.acp_group, msg_id)
        except Exception:
            logger.debug("Reclaim check failed (non-fatal)", exc_info=True)

    # ── Heartbeat + reclaim loop ──
    async def _heartbeat_loop(self) -> None:
        """Background task: refresh lock + reclaim stale messages + evict idle sessions."""
        reclaim_counter = 0
        evict_counter = 0
        while not self._shutdown:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._refresh_lock()
            reclaim_counter += 1
            if reclaim_counter * HEARTBEAT_INTERVAL >= RECLAIM_INTERVAL:
                reclaim_counter = 0
                await self._reclaim_stale()
            evict_counter += 1
            if evict_counter * HEARTBEAT_INTERVAL >= 300:  # every 5 minutes
                evict_counter = 0
                await self.pool.evict_idle()

    # ── ACP session control loop ──
    async def _control_loop(self) -> None:
        """Background task: process fork/model control messages from API."""
        import json as _json
        redis = R.get_redis()
        stream = "acp:control"
        group = "runner-control"
        consumer = "runner-0"

        try:
            await redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass

        while not self._shutdown:
            try:
                resp = await redis.xreadgroup(
                    group, consumer, {stream: ">"}, count=1, block=3000,
                )
            except Exception:
                await asyncio.sleep(2)
                continue
            if not resp:
                continue
            for _s, entries in resp:
                for entry_id, fields in entries:
                    try:
                        await redis.xack(stream, group, entry_id)
                    except Exception:
                        pass
                    raw = fields.get(b"data", fields.get("data"))
                    if not raw:
                        continue
                    try:
                        data = _json.loads(raw)
                    except Exception:
                        continue
                    t = asyncio.create_task(self._handle_control(data))
                    self._bg_tasks.add(t)
                    t.add_done_callback(self._bg_tasks.discard)

    async def _handle_control(self, data: dict) -> None:
        """Handle a single control message (fork/model)."""
        import json as _json
        ctrl_type = data.get("type")
        conv_id = data.get("conversation_id", "")
        response_channel = f"chan:control:{conv_id}"
        redis = R.get_redis()

        if ctrl_type == "fork":
            new_conv_id = data.get("new_conversation_id", "")
            client = self.pool._clients.get(conv_id)
            if client and client._session_id:
                try:
                    import os
                    cwd = os.path.join(settings.workspace_root, new_conv_id)
                    os.makedirs(cwd, exist_ok=True)
                    new_sid = await asyncio.wait_for(
                        client.fork_session(client._session_id, cwd), timeout=15,
                    )
                    await redis.publish(response_channel, _json.dumps({"session_id": new_sid}))
                    logger.info("Forked ACP session %s -> %s", client._session_id[:8], new_sid[:8])
                except Exception as e:
                    logger.error("Fork failed: %s", e)
                    await redis.publish(response_channel, _json.dumps({"error": str(e)}))
            else:
                await redis.publish(response_channel, _json.dumps({"error": "no active session"}))

        elif ctrl_type == "model":
            model_id = data.get("model_id", "")
            client = self.pool._clients.get(conv_id)
            if client and client._session_id:
                try:
                    await asyncio.wait_for(
                        client.set_session_model(client._session_id, model_id), timeout=10,
                    )
                    await redis.publish(response_channel, _json.dumps({"ok": True}))
                    logger.info("Set model %s on session %s", model_id, client._session_id[:8])
                except Exception as e:
                    logger.error("Set model failed: %s", e)
                    await redis.publish(response_channel, _json.dumps({"error": str(e)}))
            else:
                await redis.publish(response_channel, _json.dumps({"error": "no active session"}))

    # ── main loop ──
    async def run(self) -> None:
        configure_logging()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))

        try:
            await R.get_redis().ping()
        except Exception as exc:
            if settings.is_production:
                raise RuntimeError(f"Redis unreachable at startup: {exc}") from exc
            logger.warning("Redis not reachable at startup: %s", exc)

        if not await self._acquire_lock():
            return

        await self.register_agents()
        await self.ensure_group()
        logger.info("Runner consuming %s as %s/%s (max_concurrent=%d)",
                    settings.acp_stream, settings.acp_group, settings.acp_consumer, MAX_CONCURRENT)

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        control_task = asyncio.create_task(self._control_loop())

        _xread_backoff = 2.0
        try:
            while not self._shutdown:
                try:
                    resp = await R.get_redis().xreadgroup(
                        settings.acp_group,
                        settings.acp_consumer,
                        {settings.acp_stream: ">"},
                        count=1,
                        block=3000,
                    )
                    _xread_backoff = 2.0
                except Exception:
                    logger.exception("xreadgroup failed; backing off %.0fs", _xread_backoff)
                    await asyncio.sleep(_xread_backoff)
                    _xread_backoff = min(_xread_backoff * 1.5, 30.0)
                    continue

                if not resp:
                    continue
                for _stream, entries in resp:
                    for entry_id, fields in entries:
                        if self._shutdown:
                            break
                        try:
                            await R.get_redis().xack(
                                settings.acp_stream, settings.acp_group, entry_id
                            )
                        except Exception:
                            logger.warning("Failed to ACK %s", entry_id)

                        task_data = json.loads(fields["data"])
                        task = asyncio.create_task(
                            self._run_task(task_data, entry_id)
                        )
                        self._active_tasks.add(task)
                        task.add_done_callback(self._on_task_done)
        finally:
            control_task.cancel()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            if self._active_tasks:
                logger.info("Waiting for %d active task(s) to finish...", len(self._active_tasks))
                await asyncio.wait(self._active_tasks, timeout=60)
            if self._bg_tasks:
                await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            await self._release_lock()

    # ── concurrency helpers ──
    async def _run_task(self, task_data: dict, entry_id: str) -> None:
        """Run a single task with semaphore-based concurrency limiting."""
        async with self._sem:
            task_type = task_data.get("type", "unknown")
            start_time = time.monotonic()
            TASKS_ENQUEUED.labels(type=task_type).inc()
            logger.info("Starting task %s (active=%d/%d)",
                        entry_id, len(self._active_tasks), MAX_CONCURRENT)
            try:
                await self.handle(task_data)
                duration = time.monotonic() - start_time
                TASK_DURATION.labels(type=task_type).observe(duration)
                TASKS_COMPLETED.labels(type=task_type, status="success").inc()
            except Exception as exc:
                duration = time.monotonic() - start_time
                TASK_DURATION.labels(type=task_type).observe(duration)
                retries = self._task_retries.get(entry_id, 0)
                if retries < MAX_RETRIES:
                    self._task_retries[entry_id] = retries + 1
                    logger.warning("Task %s failed (retry %d/%d): %s",
                                   entry_id, retries + 1, MAX_RETRIES, exc)
                    # Re-enqueue for retry
                    try:
                        await R.get_redis().xadd(
                            settings.acp_stream, {"data": json.dumps(task_data)}
                        )
                    except Exception:
                        logger.exception("Failed to re-enqueue task %s", entry_id)
                else:
                    # Max retries reached — send to DLQ
                    logger.error("Task %s failed permanently after %d retries, sending to DLQ",
                                 entry_id, MAX_RETRIES)
                    TASKS_FAILED.labels(type=task_type, error=type(exc).__name__).inc()
                    DLQ_MESSAGES.labels(reason="max_retries").inc()
                    try:
                        await R.get_redis().xadd(
                            DLQ_STREAM,
                            {"data": json.dumps(task_data), "error": str(exc), "entry_id": entry_id},
                        )
                    except Exception:
                        logger.exception("Failed to send task %s to DLQ", entry_id)
                    self._task_retries.pop(entry_id, None)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Clean up completed tasks from the active set."""
        self._active_tasks.discard(task)
        if task.exception():
            logger.error("Task failed: %s", task.exception())

    # ── dispatch ──
    async def handle(self, task: dict) -> None:
        if task.get("type") == "roundtable":
            from agent_runner.runner_roundtable import handle_roundtable
            await handle_roundtable(task, self.agents)
            return
        if task.get("type") == "memory_consolidate":
            from agent_runner.runner_memory import handle_memory_consolidate
            await handle_memory_consolidate(task, self.agents)
            return
        if task.get("type") == "scheduled":
            from agent_runner.runner_scheduled import handle_scheduled
            await handle_scheduled(task, self.agents)
            return
        await self.handle_single(task)

    # ── one prompt (single agent) ──
    async def handle_single(self, task: dict) -> None:
        from agent_runner.runner_clarify import pop_clarify_request, handle_clarify_request

        conversation_id = task["conversation_id"]
        message_id = task["message_id"]
        agent_id = task.get("agent_id", "hermes")
        text = task["text"]
        system_prompt: str | None = task.get("system_prompt") or None
        profile_dir: str | None = task.get("profile_dir") or None

        agent = self.agents.get(agent_id) or self.agents.get("hermes")
        if agent is None:
            await self._fail(conversation_id, message_id, "没有可用的 agent")
            return

        cwd = os.path.join(settings.workspace_root, conversation_id)
        os.makedirs(cwd, exist_ok=True)

        acp_session_id = None
        session_mode = None
        async with async_session_maker() as db:
            convo = await db.get(Conversation, uuid.UUID(conversation_id))
            if convo:
                acp_session_id = convo.acp_session_id
                session_mode = convo.session_mode

        acc = {"text": "", "cancelled": False, "current_msg_id": message_id, "tool_since_split": False, "thinking": "", "plan": None, "files": []}
        steps: list[dict] = []

        async def on_update(update: dict) -> None:
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                delta = (update.get("content") or {}).get("text", "")
                if delta:
                    acc["tool_since_split"] = False
                    acc["text"] += delta
                    await R.publish_event(
                        conversation_id,
                        {"type": "token", "message_id": acc["current_msg_id"], "delta": delta},
                    )
            elif kind == "tool_call":
                acc["tool_since_split"] = True
                step = {"title": update.get("title"), "status": update.get("status")}
                steps.append(step)
                await R.publish_event(
                    conversation_id,
                    {
                        "type": "tool_call",
                        "message_id": acc["current_msg_id"],
                        "title": step["title"],
                        "status": step["status"],
                    },
                )
            elif kind == "agent_thought":
                delta = (update.get("content") or {}).get("text", "") or update.get("delta", "")
                if delta:
                    acc["thinking"] += delta
                    await R.publish_event(conversation_id, {
                        "type": "thought",
                        "message_id": acc["current_msg_id"],
                        "delta": delta,
                    })
            elif kind == "plan":
                raw = update.get("entries") or update.get("plan") or []
                if isinstance(raw, list) and raw:
                    acc["plan"] = [{"content": e.get("content", ""), "status": e.get("status", "pending"), "priority": e.get("priority", 0)} for e in raw if isinstance(e, dict)]
                    await R.publish_event(conversation_id, {
                        "type": "plan",
                        "message_id": acc["current_msg_id"],
                        "entries": [
                            {
                                "content": e.get("content", ""),
                                "status": e.get("status", "pending"),
                                "priority": e.get("priority", 0),
                            }
                            for e in raw if isinstance(e, dict)
                        ],
                    })
            elif kind == "usage":
                await R.publish_event(conversation_id, {
                    "type": "usage",
                    "message_id": acc["current_msg_id"],
                    "input_tokens": update.get("input_tokens", 0),
                    "output_tokens": update.get("output_tokens", 0),
                })
                acc["total_tokens"] = update.get("input_tokens", 0) + update.get("output_tokens", 0)
            elif kind == "session_info" or kind == "session_info_update":
                new_title = update.get("title")
                if new_title:
                    t = asyncio.create_task(self._update_conv_title(conversation_id, new_title))
                    self._bg_tasks.add(t)
                    t.add_done_callback(self._bg_tasks.discard)
                    await R.publish_event(conversation_id, {"type": "session_info", "title": new_title})
            elif kind == "usage_update":
                size = update.get("size", 0)
                used = update.get("used", 0)
                await R.publish_event(conversation_id, {
                    "type": "usage",
                    "message_id": acc["current_msg_id"],
                    "context_size": size,
                    "context_used": used,
                })
            elif kind == "confirmation_request":
                request_id = update.get("request_id", str(uuid.uuid4()))
                question = update.get("question", "需要你的确认")
                options = update.get("options", ["继续", "跳过"])
                req_payload = {
                    "id": request_id,
                    "conversation_id": conversation_id,
                    "message_id": acc["current_msg_id"],
                    "question": question,
                    "questions": [{"question": question, "options": options, "allow_free_text": True}],
                    "options": options,
                }
                await R.publish_event(
                    conversation_id,
                    {"type": "confirmation_request", "message_id": acc["current_msg_id"], "request": req_payload},
                )
                logger.info("Native confirmation_request, sent SSE: %s", request_id)
                t = asyncio.create_task(
                    self._wait_and_unblock_clarify_native(
                        conversation_id, request_id, sid=conversation_id,
                        message_id=acc["current_msg_id"], acc=acc,
                    )
                )
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)
            if not acc["cancelled"] and await R.is_cancelled(conversation_id):
                acc["cancelled"] = True
                try:
                    await client.cancel()
                except Exception:  # noqa: BLE001
                    pass

        async def on_fs_write(path: str, content: str) -> None:
            import difflib
            old_content = await storage.get_existing_content(uuid.UUID(conversation_id), path)
            f = await storage.save_file(
                uuid.UUID(conversation_id), path, content, agent_id,
                uuid.UUID(acc["current_msg_id"]),
            )
            # Also write to disk so the agent can read its own output later.
            from app.core.files import confine_to_dir, safe_relative_path
            disk_path = confine_to_dir(cwd, safe_relative_path(path))
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)
            await asyncio.to_thread(_write_text, disk_path, content)
            diff: str | None = None
            if old_content is not None:
                diff_lines = list(difflib.unified_diff(
                    old_content.splitlines(keepends=True), content.splitlines(keepends=True),
                    fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
                ))
                if diff_lines:
                    diff = "".join(diff_lines[:80])
            file_entry = {"id": str(f.id), "name": f.name, "kind": f.kind, "version": f.current_version}
            if diff:
                file_entry["diff"] = diff
            existing = [i for i, fi in enumerate(acc["files"]) if fi["id"] == file_entry["id"]]
            if existing:
                acc["files"][existing[0]] = file_entry
            else:
                acc["files"].append(file_entry)
            await R.publish_event(
                conversation_id,
                {
                    "type": "file",
                    "message_id": acc["current_msg_id"],
                    "file_id": str(f.id),
                    "name": f.name,
                    "kind": f.kind,
                    "version": f.current_version,
                    "diff": diff,
                },
            )

        await R.publish_event(
            conversation_id, {"type": "start", "message_id": message_id}
        )

        try:
            client, new_session = await self.pool.get(
                conversation_id, agent.command, cwd, on_update, on_fs_write,
                acp_session_id=acp_session_id, profile_dir=profile_dir,
            )
            logger.info(
                "handle_single: conv=%s msg=%s client_pid=%s new_session=%s",
                conversation_id[:8], message_id[:8],
                client._proc.pid if client._proc else "None", new_session,
            )
            if new_session:
                await self._set_session_id(conversation_id, new_session)
                if session_mode:
                    try:
                        await client.set_session_mode(new_session, session_mode)
                        logger.info("Applied session_mode=%s to new session %s", session_mode, new_session[:8])
                    except Exception:
                        logger.debug("Could not apply session_mode", exc_info=True)

            clarify_session_id = new_session or acp_session_id or conversation_id
            effective_text = text
            if system_prompt:
                effective_text = f"【角色设定】\n{system_prompt}\n【角色设定结束】\n\n{text}"

            content_blocks = task.get("content_blocks")
            if content_blocks:
                if system_prompt:
                    for block in content_blocks:
                        if block.get("type") == "text":
                            block["text"] = f"【角色设定】\n{system_prompt}\n【角色设定结束】\n\n{block['text']}"
                            break
                prompt_content = content_blocks
            else:
                prompt_content = effective_text
            prompt_task = asyncio.create_task(client.prompt(prompt_content))
            while not prompt_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(prompt_task), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                try:
                    data = await pop_clarify_request(clarify_session_id)
                    if data:
                        await handle_clarify_request(
                            conversation_id, acc["current_msg_id"], acc,
                            clarify_session_id, data, self._bg_tasks,
                        )
                except Exception:
                    logger.debug("clarify poll failed", exc_info=True)
            stop_reason = prompt_task.result()
        except ACPTimeout as exc:
            logger.error("prompt timed out for %s: %s", conversation_id, exc)
            await self.pool.drop(conversation_id)
            await self._fail(conversation_id, acc["current_msg_id"], f"响应超时：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("prompt failed")
            await self.pool.drop(conversation_id)
            await self._fail(conversation_id, acc["current_msg_id"], f"{exc.__class__.__name__}")
            return

        if acc["cancelled"]:
            status = "cancelled"
        elif not acc["text"].strip():
            # Agent returned empty text (e.g. context overflow → 400 → refusal).
            # Don't silently mark as complete — surface the error to the user.
            logger.warning(
                "Agent returned empty text for %s (stop_reason=%s)",
                conversation_id[:8], stop_reason,
            )
            await self._fail(
                conversation_id, acc["current_msg_id"],
                "模型返回空响应（上下文可能过长，请减少附件大小或缩短对话后重试）",
            )
            return
        else:
            status = "complete"

        if status == "complete":
            await self._extract_and_save_files(
                conversation_id, acc["current_msg_id"], agent_id, acc["text"]
            )

        await self._finalize(
            acc["current_msg_id"], acc["text"], status, steps,
            acc.get("thinking") or "", acc.get("plan"), acc.get("files"),
            acc.get("clarifies"),
        )
        await R.clear_cancel(conversation_id)
        await R.publish_event(
            conversation_id,
            {
                "type": "done",
                "message_id": acc["current_msg_id"],
                "stop_reason": stop_reason,
                "status": status,
                "text": acc["text"],
            },
        )

    async def _wait_and_unblock_clarify_native(
        self, conversation_id: str, request_id: str, *,
        sid: str, message_id: str | None = None, acc: dict | None = None,
    ) -> None:
        from agent_runner.runner_clarify import deliver_clarify_response
        try:
            resp = await R.wait_for_confirmation(
                conversation_id, request_id,
                timeout=settings.clarify_timeout_seconds, cancel_check=True,
            )
            choice = resp.get("choice", "超时")
        except Exception:
            logger.warning("Native clarify wait failed for %s", request_id[:8], exc_info=True)
            choice = "超时"
        logger.info("Native clarify response for %s: %s", request_id[:8], choice)

        try:
            await R.publish_event(
                conversation_id,
                {"type": "confirmation_response", "request_id": request_id, "choice": choice},
            )
        except Exception:
            logger.warning("Failed to publish confirmation_response", exc_info=True)

        if not await deliver_clarify_response(sid, request_id, choice):
            await asyncio.sleep(0.5)
            await deliver_clarify_response(sid, request_id, choice)

    # ── Fallback: extract files from AI text response ──
    async def _extract_and_save_files(
        self, conversation_id: str, message_id: str, agent_id: str, text: str
    ) -> None:
        """Parse AI response text for file artifacts and save them to the workspace."""

        cid = uuid.UUID(conversation_id)
        saved_names: set[str] = set()
        extracted: list[tuple[str, str]] = []

        code_block_re = re.compile(
            r"```(?:(\w+)\s+)?(\S+\.\w+)\s*\n(.*?)```",
            re.DOTALL,
        )
        for m in code_block_re.finditer(text):
            filename = m.group(2)
            content = m.group(3).strip()
            if filename and content and filename not in saved_names:
                extracted.append((filename, content))

        path_re = re.compile(
            r"(?:路径|Path|文件路径|保存到|生成到|保存在)[:：]\s*"
            r"(?:(~/)?([^\s\n]+\.\w+))",
            re.IGNORECASE,
        )
        # Confine file reads to the workspace directory to prevent the agent
        # from instructing the runner to read arbitrary host files
        # (e.g. /etc/shadow, ~/.ssh/id_rsa, the app's .env).
        ws_root = os.path.realpath(os.path.join(settings.workspace_root, conversation_id))
        for m in path_re.finditer(text):
            raw_path = m.group(0).split(":", 1)[-1].split("：", 1)[-1].strip()
            if raw_path.startswith("~/"):
                raw_path = os.path.expanduser(raw_path)
            # Resolve to absolute real path and confine to workspace.
            candidate = os.path.realpath(raw_path)
            if candidate != ws_root and not candidate.startswith(ws_root + os.sep):
                logger.warning("Refusing to read file outside workspace: %s", raw_path)
                continue
            filename = os.path.basename(raw_path)
            if filename in saved_names:
                continue
            if os.path.isfile(candidate):
                try:
                    with open(candidate, encoding="utf-8") as fh:
                        content = fh.read()
                    extracted.append((filename, content))
                except Exception:  # noqa: BLE01
                    logger.debug("Could not read %s", candidate)

        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for name, content in extracted:
            if name not in seen and name not in saved_names:
                seen.add(name)
                unique.append((name, content))

        for filename, content in unique:
            try:
                f = await storage.save_file(cid, filename, content, agent_id, uuid.UUID(message_id))
                await R.publish_event(
                    conversation_id,
                    {
                        "type": "file",
                        "message_id": message_id,
                        "file_id": str(f.id),
                        "name": f.name,
                        "kind": f.kind,
                        "version": f.current_version,
                    },
                )
                logger.info("Fallback: extracted file '%s' from AI response", filename)
            except Exception:  # noqa: BLE01
                logger.exception("Failed to save extracted file '%s'", filename)

    # ── DB writes ──
    async def _create_agent_message(self, conversation_id: str, agent_id: str) -> str:
        async with async_session_maker() as db:
            msg = Message(
                conversation_id=uuid.UUID(conversation_id),
                role="agent",
                agent_id=agent_id,
                content={"text": ""},
                status="streaming",
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return str(msg.id)

    async def _finalize(
        self, message_id: str, text: str, status: str, steps: list[dict] | None = None,
        thinking: str | None = None, plan: list[dict] | None = None,
        files: list[dict] | None = None, clarifies: list[dict] | None = None,
    ) -> None:
        async with async_session_maker() as db:
            msg = await db.get(Message, uuid.UUID(message_id))
            if msg:
                content: dict = {"text": text}
                if steps:
                    content["tool_calls"] = steps
                if thinking:
                    content["thinking"] = thinking
                if plan:
                    content["plan"] = plan
                if files:
                    content["files"] = files
                if clarifies:
                    content["clarifies"] = clarifies
                msg.content = content
                msg.status = status
                convo = await db.get(Conversation, msg.conversation_id)
                if convo:
                    convo.updated_at = datetime.now(tz=timezone.utc)
                await db.commit()

    async def _set_session_id(self, conversation_id: str, session_id: str) -> None:
        async with async_session_maker() as db:
            convo = await db.get(Conversation, uuid.UUID(conversation_id))
            if convo:
                convo.acp_session_id = session_id
                await db.commit()

    async def _update_conv_title(self, conversation_id: str, title: str) -> None:
        from sqlalchemy import update as sa_upd
        async with async_session_maker() as db:
            await db.execute(
                sa_upd(Conversation)
                .where(Conversation.id == uuid.UUID(conversation_id))
                .values(title=title)
            )
            await db.commit()

    async def _fail(self, conversation_id: str, message_id: str, detail: str) -> None:
        await self._finalize(message_id, f"⚠ 生成失败：{detail}", "error")
        await R.publish_event(
            conversation_id,
            {"type": "error", "message_id": message_id, "detail": detail},
        )
        await R.publish_event(
            conversation_id, {"type": "done", "message_id": message_id, "status": "error"}
        )


async def _amain() -> None:
    runner = Runner()
    try:
        await runner.run()
    finally:
        await runner.pool.close_all()
        await R.close_redis()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
