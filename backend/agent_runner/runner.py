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
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid
from datetime import datetime, timezone

from redis.exceptions import ResponseError

from app.config import settings
from app.core.logging import configure_logging
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.agent import Agent
from app.db.models.conversation import Conversation, Message
from agent_runner import discovery, storage
from agent_runner.acp_client import ACPClient, ACPTimeout
from agent_runner.session_pool import SessionPool

logger = logging.getLogger("hermes.runner")

# ── Stability constants ──
LOCK_KEY = "hermes:runner:lock"
LOCK_TTL = 30          # seconds; must be > heartbeat interval
HEARTBEAT_INTERVAL = 10  # refresh lock every N seconds
STALE_THRESHOLD_MS = 120_000  # 2 minutes — reclaim stuck pending messages
RECLAIM_INTERVAL = 30   # check for stale messages every N seconds
MAX_CONCURRENT = 5      # max tasks processed in parallel


class Runner:
    def __init__(self) -> None:
        self.pool = SessionPool()
        self.agents: dict[str, discovery.DiscoveredAgent] = {}
        self._shutdown = False
        self._lock_token: str | None = None
        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        self._active_tasks: set[asyncio.Task] = set()
        self._bg_tasks: set[asyncio.Task] = set()

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
            # Check if the existing lock holder is alive
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
            # Only refresh if we still own it
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
            # Atomic: delete only if value matches our token
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
        async with async_session_maker() as db:
            for a in found:
                row = await db.get(Agent, a.id)
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

    # ── Signal handling ──
    def _handle_signal(self, sig: int) -> None:
        logger.info("Received signal %s, shutting down gracefully...", sig)
        self._shutdown = True

    # ── Stale message reclaim ──
    async def _reclaim_stale(self) -> None:
        """Claim stuck pending messages that exceed STALE_THRESHOLD_MS."""
        try:
            redis = R.get_redis()
            # xautoclaim: returns [next_start_id, [(msg_id, fields), ...], deleted_ids]
            result = await redis.xautoclaim(
                settings.acp_stream, settings.acp_group,
                settings.acp_consumer, STALE_THRESHOLD_MS, "0-0",
            )
            if result and len(result) > 1:
                claimed = result[1]
                for msg_id, fields in claimed:
                    logger.warning("Reclaimed stale message %s", msg_id)
                    # ACK it so it doesn't block
                    await redis.xack(settings.acp_stream, settings.acp_group, msg_id)
                    # Re-enqueue for fresh processing
                    data = fields.get(b"data", fields.get("data"))
                    if data:
                        await redis.xadd(settings.acp_stream, {"data": data})
                        logger.info("Re-enqueued stale message %s", msg_id)
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

        # Create consumer group (ignore if exists)
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

        # Register signal handlers (async-safe)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))

        # Fail fast if Redis is down — the runner is useless without it and a
        # silent retry loop hides the real problem from operators.
        try:
            await R.get_redis().ping()
        except Exception as exc:
            if settings.is_production:
                raise RuntimeError(f"Redis unreachable at startup: {exc}") from exc
            logger.warning("Redis not reachable at startup: %s", exc)

        # Singleton check
        if not await self._acquire_lock():
            return

        await self.register_agents()
        await self.ensure_group()
        logger.info("Runner consuming %s as %s/%s (max_concurrent=%d)",
                    settings.acp_stream, settings.acp_group, settings.acp_consumer, MAX_CONCURRENT)

        # Start background heartbeat + reclaim
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
                    _xread_backoff = 2.0  # reset on success
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
                        # ACK immediately — we own this message now
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
            # Wait for all active tasks to finish (with timeout)
            if self._active_tasks:
                logger.info("Waiting for %d active task(s) to finish...", len(self._active_tasks))
                await asyncio.wait(self._active_tasks, timeout=60)
            # Wait for background clarify/title tasks
            if self._bg_tasks:
                await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            await self._release_lock()

    # ── concurrency helpers ──
    async def _run_task(self, task_data: dict, entry_id: str) -> None:
        """Run a single task with semaphore-based concurrency limiting."""
        async with self._sem:
            logger.info("Starting task %s (active=%d/%d)",
                        entry_id, len(self._active_tasks), MAX_CONCURRENT)
            try:
                await self.handle(task_data)
            except Exception:
                logger.exception("handle failed for %s", entry_id)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Clean up completed tasks from the active set."""
        self._active_tasks.discard(task)
        if task.exception():
            logger.error("Task failed: %s", task.exception())

    # ── dispatch ──
    async def handle(self, task: dict) -> None:
        if task.get("type") == "roundtable":
            await self.handle_roundtable(task)
            return
        await self.handle_single(task)

    # ── roundtable: N agents in parallel, then Hermes merge ──
    async def handle_roundtable(self, task: dict) -> None:
        conversation_id = task["conversation_id"]
        message_id = task["message_id"]
        agent_ids: list[str] = task["agents"]
        text = task["text"]

        cwd = os.path.join(settings.workspace_root, conversation_id)
        os.makedirs(cwd, exist_ok=True)

        slots = []
        for i, aid in enumerate(agent_ids):
            a = self.agents.get(aid)
            slots.append({
                "agent_id": aid, "slot": i,
                "label": a.label if a else aid,
                "color": a.color if a else "#b8852a",
                "stance": a.description if a else "",
            })
        await R.publish_event(
            conversation_id, {"type": "rt_start", "message_id": message_id, "agents": slots}
        )

        async def run_one(slot: int, aid: str) -> tuple[str, str]:
            """Run one roundtable reply. Returns (text, status) where status is
            complete | timeout | error. Partial text is preserved on failure."""
            agent = self.agents.get(aid) or self.agents.get("hermes")
            buf = {"text": ""}
            reply_status = "complete"

            async def on_update(update: dict) -> None:
                if update.get("sessionUpdate") == "agent_message_chunk":
                    d = (update.get("content") or {}).get("text", "")
                    if d:
                        buf["text"] += d
                        await R.publish_event(conversation_id, {
                            "type": "rt_token", "message_id": message_id, "slot": slot, "delta": d
                        })

            async def on_fs(path: str, content: str) -> None:
                f = await storage.save_file(uuid.UUID(conversation_id), path, content, aid, uuid.UUID(message_id))
                await R.publish_event(conversation_id, {
                    "type": "file", "message_id": message_id, "file_id": str(f.id),
                    "name": f.name, "kind": f.kind, "version": f.current_version,
                })

            client = ACPClient(
                agent.command, cwd, protocol_version=settings.acp_protocol_version,
                on_update=on_update, on_fs_write=on_fs,
            )
            try:
                await client.start()
                await client.initialize()
                await client.new_session(cwd)
                await client.prompt(text)
            except ACPTimeout as exc:
                logger.error("roundtable timeout (%s): %s", aid, exc)
                reply_status = "timeout"
                buf["text"] = buf["text"] or f"（{aid} 超时未响应）"
            except Exception:  # noqa: BLE001
                logger.exception("roundtable reply failed (%s)", aid)
                reply_status = "error"
                buf["text"] = buf["text"] or "（该助手作答失败）"
            finally:
                await client.stop()
            await R.publish_event(
                conversation_id,
                {"type": "rt_reply_done", "message_id": message_id, "slot": slot, "status": reply_status},
            )
            return buf["text"], reply_status

        results = await asyncio.gather(
            *[run_one(i, aid) for i, aid in enumerate(agent_ids)], return_exceptions=True
        )
        texts = [r[0] if isinstance(r, tuple) else "（作答失败）" for r in results]
        statuses = [r[1] if isinstance(r, tuple) else "error" for r in results]

        if await R.is_cancelled(conversation_id):
            await self._finalize_roundtable(message_id, agent_ids, texts, statuses, "", "cancelled")
            await R.clear_cancel(conversation_id)
            await R.publish_event(conversation_id, {
                "type": "done", "message_id": message_id, "status": "cancelled"
            })
            return

        # ── merge via Hermes — only over replies that actually succeeded ──
        ok_slots = [i for i, s in enumerate(statuses) if s == "complete" and texts[i].strip()]
        if not ok_slots:
            await self._finalize_roundtable(message_id, agent_ids, texts, statuses, "", "error")
            await R.clear_cancel(conversation_id)
            await R.publish_event(conversation_id, {
                "type": "error", "message_id": message_id, "detail": "所有助手均作答失败",
            })
            await R.publish_event(conversation_id, {
                "type": "done", "message_id": message_id, "status": "error"
            })
            return

        await R.publish_event(conversation_id, {"type": "merge_start", "message_id": message_id})
        merged = {"text": ""}
        if len(ok_slots) == 1:
            # A merge of one voice is that voice — skip the extra ACP round.
            merged["text"] = texts[ok_slots[0]]
            await R.publish_event(conversation_id, {
                "type": "merge_token", "message_id": message_id, "delta": merged["text"]
            })
        else:
            merge_prompt = "请综合以下各助手的观点，给出一致结论与下一步：\n\n" + "\n\n".join(
                f"【{agent_ids[i]}】{texts[i]}" for i in ok_slots
            )
            hermes = self.agents.get("hermes") or self.agents.get(agent_ids[0])

            async def on_merge(update: dict) -> None:
                if update.get("sessionUpdate") == "agent_message_chunk":
                    d = (update.get("content") or {}).get("text", "")
                    if d:
                        merged["text"] += d
                        await R.publish_event(conversation_id, {
                            "type": "merge_token", "message_id": message_id, "delta": d
                        })

            async def _noop(_p: str, _c: str) -> None:
                return None

            mclient = ACPClient(
                hermes.command, cwd, protocol_version=settings.acp_protocol_version,
                on_update=on_merge, on_fs_write=_noop,
            )
            try:
                await mclient.start()
                await mclient.initialize()
                await mclient.new_session(cwd)
                await mclient.prompt(merge_prompt)
            except ACPTimeout:
                logger.error("roundtable merge timed out")
            except Exception:  # noqa: BLE001
                logger.exception("roundtable merge failed")
            finally:
                await mclient.stop()

        await self._finalize_roundtable(message_id, agent_ids, texts, statuses, merged["text"], "complete")
        await R.clear_cancel(conversation_id)
        await R.publish_event(
            conversation_id, {"type": "done", "message_id": message_id, "status": "complete"}
        )

    async def _finalize_roundtable(
        self, message_id: str, agent_ids: list[str], texts: list[str],
        statuses: list[str], merged: str, status: str,
    ) -> None:
        async with async_session_maker() as db:
            msg = await db.get(Message, uuid.UUID(message_id))
            if msg:
                msg.content = {
                    "replies": [
                        {"agent_id": agent_ids[i], "text": texts[i], "status": statuses[i]}
                        for i in range(len(agent_ids))
                    ],
                    "merged": {"text": merged, "status": status},
                }
                msg.status = status
                convo = await db.get(Conversation, msg.conversation_id)
                if convo:
                    convo.updated_at = datetime.now(tz=timezone.utc)
                await db.commit()

    # ── one prompt (single agent) ──
    async def handle_single(self, task: dict) -> None:
        conversation_id = task["conversation_id"]
        message_id = task["message_id"]
        agent_id = task.get("agent_id", "hermes")
        text = task["text"]
        system_prompt: str | None = task.get("system_prompt") or None

        agent = self.agents.get(agent_id) or self.agents.get("hermes")
        if agent is None:
            await self._fail(conversation_id, message_id, "没有可用的 agent")
            return

        cwd = os.path.join(settings.workspace_root, conversation_id)
        os.makedirs(cwd, exist_ok=True)

        # Load existing ACP session ID and mode for resume
        acp_session_id = None
        session_mode = None
        async with async_session_maker() as db:
            convo = await db.get(Conversation, uuid.UUID(conversation_id))
            if convo:
                acp_session_id = convo.acp_session_id
                session_mode = convo.session_mode

        acc = {"text": "", "cancelled": False, "current_msg_id": message_id, "tool_since_split": False, "thinking": "", "plan": None, "files": []}
        steps: list[dict] = []  # Collect tool_call steps for persistence

        async def on_update(update: dict) -> None:
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                delta = (update.get("content") or {}).get("text", "")
                if delta:
                    # No split: accumulate all text (before and after tool calls) into one message
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
                # NOTE: clarify is detected solely via the request LIST poll in the
                # prompt loop (atomic LPOP) — title matching here double-fired.
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
                # v0.16.0: session_info_update (camelCase: sessionUpdate="session_info_update")
                # Legacy: session_info
                new_title = update.get("title")
                if new_title:
                    t = asyncio.create_task(self._update_conv_title(conversation_id, new_title))
                    self._bg_tasks.add(t)
                    t.add_done_callback(self._bg_tasks.discard)
                    await R.publish_event(conversation_id, {"type": "session_info", "title": new_title})
            elif kind == "usage_update":
                # v0.16.0: ACP native context usage (size=window, used=pressure)
                size = update.get("size", 0)
                used = update.get("used", 0)
                await R.publish_event(conversation_id, {
                    "type": "usage",
                    "message_id": acc["current_msg_id"],
                    "context_size": size,
                    "context_used": used,
                })
            elif kind == "confirmation_request":
                # Agent natively sent a confirmation_request via session/update.
                # Send SSE to frontend and wait for user response via background task.
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
                # Wait for user response in background, then inject result into agent
                t = asyncio.create_task(
                    self._wait_and_unblock_clarify(
                        conversation_id, request_id, sid=conversation_id,
                        message_id=acc["current_msg_id"], acc=acc,
                    )
                )
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)
            # Cooperative cancellation between chunks.
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
            diff: str | None = None
            if old_content is not None:
                diff_lines = list(difflib.unified_diff(
                    old_content.splitlines(keepends=True), content.splitlines(keepends=True),
                    fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
                ))
                if diff_lines:
                    diff = "".join(diff_lines[:80])
            # Track file for persistence in message content
            file_entry = {"id": str(f.id), "name": f.name, "kind": f.kind, "version": f.current_version}
            if diff:
                file_entry["diff"] = diff
            # Update or append (dedup by file id)
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
                acp_session_id=acp_session_id,
            )
            logger.info(
                "handle_single: conv=%s msg=%s client_pid=%s new_session=%s",
                conversation_id[:8], message_id[:8],
                client._proc.pid if client._proc else "None", new_session,
            )
            if new_session:
                await self._set_session_id(conversation_id, new_session)
                # Apply session mode (edit approval policy) if set
                if session_mode:
                    try:
                        await client.set_session_mode(new_session, session_mode)
                        logger.info("Applied session_mode=%s to new session %s", session_mode, new_session[:8])
                    except Exception:
                        logger.debug("Could not apply session_mode", exc_info=True)

            # Run prompt with concurrent clarify polling.
            # The agent's clarify_callback blocks the agent thread while waiting
            # for user response. Since it can't send on_update events while
            # blocked, we must poll Redis for pending clarify requests.
            # NOTE: agent keys clarify requests by ACP session_id — on a resumed
            # session new_session is None, so fall back to the stored session id
            # (using conversation_id there made the keys never match).
            clarify_session_id = new_session or acp_session_id or conversation_id
            # Prepend system_prompt on the first message of a new session,
            # or when system_prompt is provided (profile switch)
            effective_text = text
            if system_prompt:
                effective_text = f"【角色设定】\n{system_prompt}\n【角色设定结束】\n\n{text}"

            # Use content_blocks if available (ACP structured content), else plain text
            content_blocks = task.get("content_blocks")
            if content_blocks:
                # Inject system_prompt into the first text block
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
                # Atomically pop pending clarify requests (agent keys by session_id).
                # LPOP consumes each request exactly once — no double-trigger, and
                # queued requests survive until we get to them (no overwrite).
                try:
                    data = await self._pop_clarify_request(clarify_session_id)
                    if data:
                        await self._handle_clarify_request(
                            conversation_id, acc["current_msg_id"], acc,
                            clarify_session_id, data,
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

        status = "cancelled" if acc["cancelled"] else "complete"
        # ── Fallback: extract files from text if agent didn't use fs/write_text_file ──
        if status == "complete" and acc["text"]:
            await self._extract_and_save_files(
                conversation_id, acc["current_msg_id"], agent_id, acc["text"]
            )

        # Clarify requests are consumed during the prompt loop (LPOP + modal).
        # The legacy [确认]...[/确认] text-marker fallback was removed: it
        # contradicted the prompt rules ("禁止输出 [确认] 标记") and markers can
        # be split across streamed chunks — the tool-call path is the only one.
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

    # ── Clarify (protocol v2: LIST + BLPOP, race-free) ──
    #
    # agent RPUSH `hermes:clarify:req:{sid}`  →  runner LPOP (prompt loop)
    # runner RPUSH `hermes:clarify:resp:{sid}:{clarify_id}`  →  agent BLPOP
    #
    # BLPOP returns even when the response was pushed before the agent started
    # waiting, so auto-resolve can never strand the agent. With
    # settings.clarify_protocol == "dual" the legacy GET/pubsub keys are also
    # consumed/written for agent deployments that haven't been re-patched yet.

    async def _pop_clarify_request(self, sid: str) -> dict | None:
        """Atomically consume one pending clarify request, if any."""
        r = R.get_redis()
        val = await r.lpop(R.clarify_req_key(sid))
        if val is None and settings.clarify_protocol == "dual":
            val = await r.getdel(f"hermes:clarify_pending:{sid}")
        if not val:
            return None
        try:
            return json.loads(val)
        except (TypeError, ValueError):
            logger.warning("Malformed clarify request for sid=%s: %r", sid[:8], val)
            return None

    async def _deliver_clarify_response(self, sid: str, clarify_id: str, choice: str) -> bool:
        """Unblock the agent's clarify_callback with the chosen answer."""
        try:
            r = R.get_redis()
            resp_key = R.clarify_resp_key(sid, clarify_id)
            pipe = r.pipeline()
            pipe.rpush(resp_key, choice)
            pipe.expire(resp_key, 60)
            if settings.clarify_protocol == "dual":
                pipe.set(f"hermes:clarify_response:{sid}:{clarify_id}", choice, ex=60)
                pipe.publish(f"hermes:clarify_notify:{sid}", clarify_id)
            await pipe.execute()
            return True
        except Exception:
            logger.exception("Failed to deliver clarify response sid=%s id=%s", sid[:8], clarify_id[:8])
            return False

    def _classify_clarify_risk(self, question: str, options: list[str]) -> str:
        """Keyword risk classification for clarify questions: low | medium | high.

        Keyword lists live in settings (clarify_*_keywords) so deployments can
        tune them. Misclassification is safe: medium/high only means "show the
        modal", and the failure mode of auto-resolve is also "show the modal".
        """
        q_lower = question.lower()
        opts_lower = [opt.lower() for opt in options]
        combined = f"{q_lower} {' '.join(opts_lower)}"

        for kw in settings.clarify_high_risk_keywords:
            if kw.lower() in combined:
                return "high"
        for kw in settings.clarify_medium_risk_keywords:
            if kw.lower() in combined:
                return "medium"
        for p in settings.clarify_low_risk_patterns:
            p = p.lower()
            if p in q_lower or any(p in opt for opt in opts_lower):
                return "low"

        # Heuristic: yes/no or OK/Cancel pairs are low-risk
        if len(options) <= 2:
            yes_no = {"是", "否", "yes", "no", "ok", "cancel", "跳过", "好的", "不用", "不"}
            if all(any(w in opt.lower() for w in yes_no) for opt in options):
                return "low"

        return "medium"

    async def _handle_clarify_request(
        self, conversation_id: str, message_id: str, acc: dict, sid: str, data: dict
    ) -> None:
        """Route one popped clarify request per the configured strategy.

        Strategies:
          - disabled / auto_first : auto-resolve immediately (no UI)
          - smart                 : risk-based (low=auto, medium/high=modal)
          - interactive           : always pop confirmation modal
        Auto-resolve failures fall back to the modal — never strand the agent.
        """
        strategy = (settings.clarify_strategy or "smart").strip().lower()
        clarify_id = data.get("clarify_id") or uuid.uuid4().hex[:12]
        question = data.get("question") or "需要确认"
        options = data.get("options") or ["继续", "跳过"]

        auto = strategy in ("disabled", "auto_first") or (
            strategy == "smart" and self._classify_clarify_risk(question, options) == "low"
        )
        if auto:
            choice = options[0] if options else "继续"
            if await self._deliver_clarify_response(sid, clarify_id, choice):
                logger.info(
                    "Auto-resolved clarify sid=%s strategy=%s choice=%s",
                    sid[:8], strategy, choice,
                )
                await self._record_clarify(message_id, acc, {
                    "id": clarify_id, "question": question, "options": options,
                    "status": "auto", "choice": choice,
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                })
                await R.publish_event(conversation_id, {
                    "type": "clarify_auto", "message_id": message_id,
                    "question": question, "choice": choice,
                })
                return
            logger.warning("Auto-resolve delivery failed, falling back to modal (sid=%s)", sid[:8])

        # ── Interactive modal (interactive, smart medium/high, or auto fallback) ──
        await self._record_clarify(message_id, acc, {
            "id": clarify_id, "question": question, "options": options,
            "status": "pending", "ts": datetime.now(tz=timezone.utc).isoformat(),
        })
        req_payload = {
            "id": clarify_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "question": question,
            "questions": [{"question": question, "options": options, "allow_free_text": True}],
            "options": options,
        }
        await R.publish_event(
            conversation_id,
            {"type": "confirmation_request", "message_id": message_id, "request": req_payload},
        )
        logger.info("Clarify request, sent confirmation_request: %s (sid=%s)", clarify_id, sid[:8])

        t = asyncio.create_task(
            self._wait_and_unblock_clarify(
                conversation_id, clarify_id, sid=sid, message_id=message_id, acc=acc
            )
        )
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)

    async def _wait_and_unblock_clarify(
        self, conversation_id: str, clarify_id: str, *,
        sid: str, message_id: str | None = None, acc: dict | None = None,
    ) -> None:
        """Wait for the user's modal answer (or cancel/timeout), then unblock
        the agent's clarify_callback and persist the outcome."""
        try:
            resp = await R.wait_for_confirmation(
                conversation_id, clarify_id,
                timeout=settings.clarify_timeout_seconds, cancel_check=True,
            )
            choice = resp.get("choice", "超时")
        except Exception:
            logger.warning("Clarify wait failed for %s", clarify_id[:8], exc_info=True)
            choice = "超时"
        logger.info("Clarify response for %s: %s", clarify_id[:8], choice)

        try:
            await R.publish_event(
                conversation_id,
                {"type": "confirmation_response", "request_id": clarify_id, "choice": choice},
            )
        except Exception:
            logger.warning("Failed to publish confirmation_response", exc_info=True)

        if not await self._deliver_clarify_response(sid, clarify_id, choice):
            await asyncio.sleep(0.5)
            await self._deliver_clarify_response(sid, clarify_id, choice)

        if message_id and acc is not None:
            status = {"已取消": "cancelled", "超时": "timeout"}.get(choice, "answered")
            await self._update_clarify(message_id, acc, clarify_id, status, choice)

    # ── Clarify persistence: Q&A lives in message.content["clarifies"] so a
    # page refresh can restore the pending modal and history keeps the audit. ──

    async def _record_clarify(self, message_id: str, acc: dict, entry: dict) -> None:
        acc.setdefault("clarifies", []).append(entry)
        await self._write_clarifies(message_id, acc["clarifies"])

    async def _update_clarify(
        self, message_id: str, acc: dict, clarify_id: str, status: str, choice: str
    ) -> None:
        for e in acc.get("clarifies", []):
            if e.get("id") == clarify_id:
                e["status"] = status
                e["choice"] = choice
        await self._write_clarifies(message_id, acc.get("clarifies", []))

    async def _write_clarifies(self, message_id: str, clarifies: list[dict]) -> None:
        try:
            async with async_session_maker() as db:
                msg = await db.get(Message, uuid.UUID(message_id))
                if msg:
                    msg.content = {**(msg.content or {}), "clarifies": clarifies}
                    await db.commit()
        except Exception:
            logger.warning("Failed to persist clarifies for %s", message_id[:8], exc_info=True)

    # ── Fallback: extract files from AI text response ──
    async def _extract_and_save_files(
        self, conversation_id: str, message_id: str, agent_id: str, text: str
    ) -> None:
        """Parse AI response text for file artifacts and save them to the workspace.

        Catches two patterns:
        1. Fenced code blocks with a filename hint (```python filename.py or ```filename.txt)
        2. Explicit file path mentions (路径: ~/Downloads/xxx, 文件已生成 + path)

        Files already saved via fs/write_text_file in the CURRENT message are skipped (dedup).
        Files from PREVIOUS messages are still updated (creates version history).
        """
        import re

        cid = uuid.UUID(conversation_id)

        # Track files saved via fs/write_text_file in THIS message only (from on_fs_write)
        # We use a set passed from the caller or track via message_id
        saved_names: set[str] = set()

        extracted: list[tuple[str, str]] = []  # (filename, content)

        # Pattern 1: fenced code blocks with filename
        # Matches: ```python filename.py\n...``` or ```filename.txt\n...```
        code_block_re = re.compile(
            r"```(?:(\w+)\s+)?(\S+\.\w+)\s*\n(.*?)```",
            re.DOTALL,
        )
        for m in code_block_re.finditer(text):
            filename = m.group(2)
            content = m.group(3).strip()
            if filename and content and filename not in saved_names:
                extracted.append((filename, content))

        # Pattern 2: explicit file path mention + content in the text
        # Look for "路径: ~/Downloads/xxx" or "文件已生成" patterns, then try to read the file
        path_re = re.compile(
            r"(?:路径|Path|文件路径|保存到|生成到|保存在)[:：]\s*"
            r"(?:(~/)?([^\s\n]+\.\w+))",
            re.IGNORECASE,
        )
        for m in path_re.finditer(text):
            raw_path = m.group(0).split(":", 1)[-1].split("：", 1)[-1].strip()
            if raw_path.startswith("~/"):
                raw_path = os.path.expanduser(raw_path)
            filename = os.path.basename(raw_path)
            if filename in saved_names:
                continue
            # Try to read the file if it exists on disk
            if os.path.isfile(raw_path):
                try:
                    with open(raw_path, encoding="utf-8") as fh:
                        content = fh.read()
                    extracted.append((filename, content))
                except Exception:  # noqa: BLE01
                    logger.debug("Could not read %s", raw_path)

        # Deduplicate (keep first occurrence)
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for name, content in extracted:
            if name not in seen and name not in saved_names:
                seen.add(name)
                unique.append((name, content))

        # Save extracted files
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

    # ── DB writes (off the hot path) ──
    async def _create_agent_message(self, conversation_id: str, agent_id: str) -> str:
        """Create a new agent message in DB, return its ID."""
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
                # touch the conversation's updated_at
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
