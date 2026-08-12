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

from sqlalchemy import select

from app.config import settings
from app.core.logging import configure_logging
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.agent import Agent
from app.db.models.conversation import Conversation, Message
from agent_runner import discovery, storage
from agent_runner.acp_client import ACPTimeout
from agent_runner.call_log import CallCollector
from agent_runner.session_pool import SessionPool
from agent_runner.subagent_pool import SubagentPool
from agent_runner.workspace_watcher import WorkspaceWatcher
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


def _read_text(path: str) -> str:
    """Synchronous file read for use with asyncio.to_thread."""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ANSI escape code remover: colours, cursor, private mode, OSC hyperlinks,
# charset sequences, and single-char sequences. CLI tools commonly emit these.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]"        # CSI + private mode (e.g. \x1b[?25l)
    r"|\x1b\][0-9;]*(?:[^\x1b]*\x1b\\|[^\x07]*\x07)"  # OSC (hyperlink, title)
    r"|\x1b[()][0-9A-Za-z]"          # Charset (e.g. \x1b(B)
    r"|\x1b[0-9A-Za-z]",             # Single-char (e.g. \x1b7)
)


def _strip_ansi(text: str | None) -> str:
    if not text:
        return ""
    if not isinstance(text, str):
        # tool_call rawInput / artifact content can be structured objects
        # (dict/list) rather than text — serialize so the UI preview stays
        # readable instead of crashing the turn.
        import json
        text = json.dumps(text, ensure_ascii=False)
        # json.dumps escapes control chars (\x1b → \u001b); restore them so
        # the ANSI regex below can actually strip terminal codes.
        text = text.replace("\\u001b", "\x1b")
    return _ANSI_RE.sub("", text)


def _friendly_error(exc: Exception) -> str:
    """P7: map exception classes to user-readable failure messages instead of
    exposing bare class names (ACPError, UnboundLocalError, …)."""
    name = exc.__class__.__name__
    msg = str(exc) or ""
    low = msg.lower()
    if "acp" in name.lower() or "acp" in low:
        return f"Agent 通信异常：{msg[:120]}" if msg else "Agent 通信异常"
    if "timeout" in name.lower() or "timed out" in low:
        return "响应超时"
    if "connection" in name.lower() or "redis" in name.lower():
        return "服务连接异常，请重试"
    if name in ("TypeError", "ValueError", "UnboundLocalError", "KeyError", "IndexError"):
        return f"处理异常（{name}），请重试"
    return f"{name}：{msg[:120]}" if msg else name


# ── Stability constants ──
LOCK_KEY = "hermes:runner:lock"
LOCK_TTL = 30          # seconds; must be > heartbeat interval
HEARTBEAT_INTERVAL = 10  # refresh lock every N seconds
STALE_THRESHOLD_MS = 120_000  # 2 minutes — reclaim stuck pending messages
RECLAIM_INTERVAL = 30   # check for stale messages every N seconds
MAX_CONCURRENT = 10     # max tasks processed in parallel
MAX_RETRIES = 3         # max retries before sending to DLQ
DLQ_STREAM = "acp:dlq"  # dead letter queue stream
RETRY_BACKOFF_BASE = 2  # exponential backoff base: 2^attempt seconds, capped at 60


class Runner:
    def __init__(self) -> None:
        self.pool = SessionPool()
        self.subagent_pool = SubagentPool()
        self.agents: dict[str, discovery.DiscoveredAgent] = {}
        self._shutdown = False
        self._lock_token: str | None = None
        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        self._active_tasks: set[asyncio.Task] = set()
        self._bg_tasks: set[asyncio.Task] = set()
        self._watchers: dict[str, WorkspaceWatcher] = {}  # conversation_id -> watcher
        # Per-conversation turn serialization (see _run_task docstring).
        self._conv_locks: dict[str, asyncio.Lock] = {}

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
    async def _maybe_ingest_hermes_skills(self) -> None:
        """Direction B auto-sync: ingest agent-created skills from the hermes
        filesystem, at most once per day (cooldown tracked in Redis).

        The loop otherwise only closes when an admin manually hits
        /memory/skills/scan. Runs on runner startup and daily thereafter;
        failures are logged, never fatal. Skills are attributed to the first
        super_admin (no interactive user exists inside the runner).
        """
        if not settings.hermes_skills_sync_enabled:
            return
        r = R.get_redis()
        cooldown_key = "hermes:skills:last-ingest"
        try:
            last_raw = await r.get(cooldown_key)
            if last_raw and time.time() - float(last_raw) < 24 * 3600:
                return
        except Exception:  # noqa: BLE001 — Redis hiccup: proceed with the scan
            logger.debug("Could not read skill-ingest cooldown", exc_info=True)

        from app.db.models.user import User
        async with async_session_maker() as db:
            admin = (await db.execute(
                select(User).where(User.role == "super_admin").limit(1)
            )).scalar_one_or_none()
            if admin is None:
                logger.warning("No super_admin user — skipping auto skill ingest")
                return
            from app.services.skill_sync_service import ingest_hermes_skills
            result = await ingest_hermes_skills(db, admin.id)
        logger.info("Auto skill ingest (Direction B): %s", result)
        try:
            await r.set(cooldown_key, str(time.time()))
        except Exception:  # noqa: BLE001
            pass

    async def _daily_skills_scan_loop(self) -> None:
        """Hourly wake-up for Direction B; the 24h Redis cooldown inside
        _maybe_ingest_hermes_skills gates the actual scan."""
        while not self._shutdown:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                return
            try:
                await self._maybe_ingest_hermes_skills()
            except Exception:
                logger.exception("Failed to auto-ingest hermes skills (daily)")

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
        # Pre-spawn warm clients for fast cold-start on new conversations.
        self.pool.set_agents(self.agents)
        asyncio.create_task(self.pool.warmup())

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

    async def _sweep_subagents(self) -> None:
        try:
            from agent_runner.runner_subagent import sweep_expired_subagents
            await sweep_expired_subagents(self.subagent_pool)
        except Exception:
            logger.debug("subagent sweep failed (non-fatal)", exc_info=True)

    # ── Heartbeat + reclaim loop ──
    async def _heartbeat_loop(self) -> None:
        """Background task: refresh lock + reclaim stale messages + evict idle sessions."""
        reclaim_counter = 0
        evict_counter = 0
        while not self._shutdown:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._refresh_lock()
            # Publish warm-pool stats (per-profile ACP availability) for the
            # admin 健康检查 console; TTL 30s so a dead runner reads stale.
            try:
                await R.set_runner_pool_stats(self.pool.pool_stats())
            except Exception:  # noqa: BLE001
                logger.debug("Failed to publish warm-pool stats", exc_info=True)
            reclaim_counter += 1
            if reclaim_counter * HEARTBEAT_INTERVAL >= RECLAIM_INTERVAL:
                reclaim_counter = 0
                await self._reclaim_stale()
            evict_counter += 1
            if evict_counter * HEARTBEAT_INTERVAL >= 300:  # every 5 minutes
                evict_counter = 0
                await self.pool.evict_idle()
                # Also stop watchers for idle conversations
                active_conv_ids = set(self.pool._clients.keys())
                for cid in list(self._watchers.keys()):
                    if cid not in active_conv_ids:
                        self._watchers[cid].stop()
                        del self._watchers[cid]
            await self._sweep_subagents()

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
                    raw = fields.get(b"data", fields.get("data"))
                    if not raw:
                        try:
                            await redis.xack(stream, group, entry_id)
                        except Exception:
                            pass
                        continue
                    try:
                        data = _json.loads(raw)
                    except Exception:
                        try:
                            await redis.xack(stream, group, entry_id)
                        except Exception:
                            pass
                        continue
                    # ACK only AFTER the control message is handled — acking
                    # first would drop fork/model requests that raise (they
                    # have no retry/DLQ; the frontend would time out forever
                    # waiting on chan:control:{id}).
                    try:
                        await self._handle_control(data)
                    finally:
                        try:
                            await redis.xack(stream, group, entry_id)
                        except Exception:
                            pass

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
                    await asyncio.to_thread(os.makedirs, cwd, exist_ok=True)
                    mcp_servers = self.pool._mcp_servers.get(conv_id)
                    new_sid = await asyncio.wait_for(
                        client.fork_session(client._session_id, cwd, mcp_servers=mcp_servers),
                        timeout=15,
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

        elif ctrl_type == "subagent_stop":
            subagent_id = data.get("subagent_id", "")
            if subagent_id:
                from agent_runner.runner_subagent import stop_subagent
                await stop_subagent(subagent_id, self.subagent_pool)

    # ── main loop ──
    async def _reclaim_stuck_streaming(self) -> None:
        """Mark leftover 'streaming' agent messages as failed.

        Runs once at startup: with no tasks executing, every streaming message
        is the remnant of a crashed/stopped runner. Without this the UI shows
        an eternal spinner and the conversation can't continue cleanly.
        """
        from datetime import timedelta
        from sqlalchemy import select as sa_sel
        from sqlalchemy import update as sa_upd
        async with async_session_maker() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
            rows = await db.execute(
                sa_sel(Message.id, Message.content).where(
                    Message.status == "streaming", Message.updated_at < cutoff
                )
            )
            ids: list = []
            for mid, content in rows.all():
                content = dict(content or {})
                content["text"] = "⚠ 生成中断（服务重启）"
                await db.execute(
                    sa_upd(Message)
                    .where(Message.id == mid)
                    .values(status="error", content=content)
                )
                ids.append(mid)
            if ids:
                await db.commit()
                logger.warning("Reclaimed %d stuck streaming message(s)", len(ids))

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
        # Direction B auto-sync: ingest agent-created skills from the hermes
        # filesystem once per day (cooldown tracked in Redis). Without this
        # the loop only closes when an admin manually hits /memory/skills/scan.
        try:
            await self._maybe_ingest_hermes_skills()
        except Exception:
            logger.exception("Failed to auto-ingest hermes skills at startup")
        try:
            from agent_runner.runner_subagent import reconcile_background_subagents
            await reconcile_background_subagents()
        except Exception:
            logger.exception("Failed to reconcile background subagents at startup")
        # C2: reclaim messages left in "streaming" by a crashed runner — no
        # task is executing at startup, so any streaming message is a leftover
        # that would otherwise pin the UI spinner forever.
        try:
            await self._reclaim_stuck_streaming()
        except Exception:
            logger.exception("Failed to reclaim stuck streaming messages at startup")
        logger.info("Runner consuming %s as %s/%s (max_concurrent=%d)",
                    settings.acp_stream, settings.acp_group, settings.acp_consumer, MAX_CONCURRENT)

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        control_task = asyncio.create_task(self._control_loop())
        skills_scan_task = asyncio.create_task(self._daily_skills_scan_loop())

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
            skills_scan_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            try:
                await skills_scan_task
            except asyncio.CancelledError:
                pass
            if self._active_tasks:
                logger.info("Waiting for %d active task(s) to finish...", len(self._active_tasks))
                await asyncio.wait(self._active_tasks, timeout=60)
            if self._bg_tasks:
                await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            await self.subagent_pool.close_all()
            for w in list(self._watchers.values()):
                w.stop()
            self._watchers.clear()
            await self._release_lock()

    # ── concurrency helpers ──
    async def _run_task(self, task_data: dict, entry_id: str) -> None:
        """Run a single task with semaphore-based concurrency limiting.

        Retry tracking uses an ``_attempt`` counter embedded in ``task_data``
        (not ``entry_id``) because re-enqueueing via ``xadd`` creates a *new*
        stream entry with a *new* id -- keying on ``entry_id`` would reset the
        counter to zero on every retry, so the DLQ would never be reached.

        Per-conversation serialization: turns on the SAME conversation are
        queued behind an asyncio.Lock so two concurrent turns can never share
        one ACPClient (its on_update callback is rebound per task — concurrent
        turns would cross-wire token events and corrupt both replies). The
        wait happens OUTSIDE the semaphore so a queued turn on a busy
        conversation doesn't consume a global concurrency slot. The lock dict
        grows by one entry per conversation ever seen (tens of bytes each) —
        acceptable for a self-hosted deployment; never cleaned to avoid the
        "waiters still present" race on delete.
        """
        conv_id = task_data.get("conversation_id")
        conv_lock = self._get_conv_lock(conv_id) if conv_id else None
        lock_acquired = False
        retry_delay: float | None = None
        if conv_lock:
            try:
                await conv_lock.acquire()
                lock_acquired = True
            except asyncio.CancelledError:
                return  # cancelled while queued behind another turn
        try:
            async with self._sem:
                task_type = task_data.get("type", "unknown")
                attempt = task_data.get("_attempt", 0)
                start_time = time.monotonic()
                TASKS_ENQUEUED.labels(type=task_type).inc()
                logger.info("Starting task %s (attempt %d, active=%d/%d)",
                            entry_id, attempt + 1, len(self._active_tasks), MAX_CONCURRENT)
                try:
                    await self.handle(task_data)
                    duration = time.monotonic() - start_time
                    TASK_DURATION.labels(type=task_type).observe(duration)
                    TASKS_COMPLETED.labels(type=task_type, status="success").inc()
                except Exception as exc:
                    duration = time.monotonic() - start_time
                    TASK_DURATION.labels(type=task_type).observe(duration)
                    if attempt < MAX_RETRIES:
                        next_attempt = attempt + 1
                        logger.warning(
                            "Task %s failed (retry %d/%d): %s",
                            entry_id, next_attempt, MAX_RETRIES, exc,
                            exc_info=True,
                        )
                        # Persist the attempt counter in the task payload so the
                        # re-enqueued message carries it across the new entry_id.
                        retry_data = {**task_data, "_attempt": next_attempt}
                        try:
                            await R.get_redis().xadd(
                                settings.acp_stream, {"data": json.dumps(retry_data)}
                            )
                        except Exception:
                            logger.exception("Failed to re-enqueue task %s", entry_id)
                        # Exponential backoff BEFORE the next attempt — applied
                        # OUTSIDE the semaphore and the per-conversation lock
                        # (deferred to the finally below) so a sleeping retry
                        # never occupies a concurrency slot.
                        retry_delay = min(RETRY_BACKOFF_BASE ** next_attempt, 60)
                    else:
                        # Max retries reached - send to DLQ
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
                        # The turn is gone for good — close out the conversation
                        # message so the UI is not stuck in "streaming" forever
                        # (which locks the conversation against new messages).
                        await self._mark_task_failed(task_data, exc)
        finally:
            if lock_acquired:
                conv_lock.release()
            if retry_delay:
                await asyncio.sleep(retry_delay)

    def _get_conv_lock(self, conv_id: str) -> asyncio.Lock:
        """Per-conversation turn lock (see _run_task docstring)."""
        lock = self._conv_locks.get(conv_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conv_locks[conv_id] = lock
        return lock

    async def _mark_task_failed(self, task_data: dict, exc: Exception) -> None:
        """Idempotently close out a conversation message left in "streaming"
        when a turn died outside the normal finalize path (e.g. an escaped
        exception that exhausted the retry budget and landed in the DLQ).

        The UI treats a streaming message as "conversation busy" — without
        this the conversation stays locked against new messages forever.
        """
        conv_id = task_data.get("conversation_id")
        msg_id = task_data.get("message_id")
        if not conv_id or not msg_id:
            return
        try:
            async with async_session_maker() as db:
                m = await db.get(Message, uuid.UUID(str(msg_id)))
                if m is not None and m.status == "streaming":
                    m.status = "error"
                    content = dict(m.content or {})
                    content["error"] = _friendly_error(exc)
                    m.content = content
                    await db.commit()
            await R.publish_event(
                str(conv_id),
                {"type": "error", "message_id": str(msg_id), "detail": _friendly_error(exc)},
            )
            await R.publish_event(
                str(conv_id), {"type": "done", "message_id": str(msg_id), "status": "error"}
            )
        except Exception:
            logger.exception(
                "Failed to mark task failed for conv=%s msg=%s", conv_id, msg_id,
            )

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
        if task.get("type") == "task_execution":
            from agent_runner.runner_task_execution import handle_task_execution
            await handle_task_execution(task, self.agents)
            return
        if task.get("type") == "skill_evolution":
            from agent_runner.runner_skill_evolution import handle_skill_evolution
            await handle_skill_evolution(task, self.agents)
            return
        if task.get("type") == "profile_evolution":
            from agent_runner.runner_profile_evolution import handle_profile_evolution
            await handle_profile_evolution(task, self.agents)
            return
        if task.get("type") == "chain":
            from agent_runner.runner_chain import handle_chain
            await handle_chain(task, self.agents)
            return
        if task.get("type") == "conversation_summary":
            # P1-2: background summarisation. Detached like subagents so a slow
            # LLM call never blocks the chat concurrency slots.
            from agent_runner.runner_conversation_summary import handle_conversation_summary
            t = asyncio.create_task(handle_conversation_summary(task))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
            return
        if task.get("type") == "knowledge_docling_upgrade":
            # Async Docling upgrade: re-extract knowledge content with Docling
            # (high-quality Markdown) in the background, then update status.
            from agent_runner.runner_docling_upgrade import handle_docling_upgrade
            t = asyncio.create_task(handle_docling_upgrade(task))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
            return
        if task.get("type") == "workspace_docling_upgrade":
            # Async Docling upgrade for a conversation workspace file (pptx):
            # writes high-quality Markdown into content_md for AI injection
            # without touching the preview HTML in `content`.
            from agent_runner.runner_docling_upgrade import handle_workspace_docling_upgrade
            t = asyncio.create_task(handle_workspace_docling_upgrade(task))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
            return
        if task.get("type") in ("subagent_spawn", "subagent_send"):
            # Fire-and-forget: a persistent subagent can run far longer than a
            # normal chat turn, so it must not hold a MAX_CONCURRENT semaphore
            # slot for its whole lifetime the way handle_single/handle_roundtable
            # do. Detach it onto _bg_tasks and return immediately, freeing the
            # slot; the subagent keeps running independently in the pool.
            from agent_runner.runner_subagent import handle_subagent_send, handle_subagent_spawn
            fn = handle_subagent_spawn if task["type"] == "subagent_spawn" else handle_subagent_send
            t = asyncio.create_task(fn(task, self.agents, self.subagent_pool))
            self._bg_tasks.add(t)
            t.add_done_callback(self._bg_tasks.discard)
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
        mcp_servers: list | None = task.get("mcp_servers") or None
        matched_skill_ids: list = task.get("matched_skill_ids") or []
        skill_firing_excerpt: str = task.get("skill_firing_excerpt") or ""

        agent = self.agents.get(agent_id) or self.agents.get("hermes")
        if agent is None:
            await self._fail(conversation_id, message_id, "没有可用的 agent")
            return

        cwd = os.path.join(settings.workspace_root, conversation_id)
        await asyncio.to_thread(os.makedirs, cwd, exist_ok=True)

        acp_session_id = None
        session_mode = None
        async with async_session_maker() as db:
            convo = await db.get(Conversation, uuid.UUID(conversation_id))
            if convo:
                acp_session_id = convo.acp_session_id
                session_mode = convo.session_mode

        # Use profile-specific session namespace so that different agents in a
        # group chat don't share the same ACP history.
        profile_id = task.get("profile_id") or ""
        # P1-3 staged: each stage gets its own session namespace + Redis key,
        # because the tool subset (mcpServers) differs per stage and ACP only
        # accepts mcpServers at session creation. Switching stages thus forces
        # a fresh session (the pool also respawns when mcp_servers change).
        stage = task.get("stage") or ""
        ns_parts = [p for p in (profile_id, stage) if p]
        session_namespace = ":".join(ns_parts)

        # If a profile-specific session exists in Redis, use it instead of the
        # conversation-level one so that group-chat agents are isolated.
        # When switching to a different profile, NEVER resume the old
        # conversation-level session (it may belong to a different profile/env
        # and hermes CLI will refuse with stop_reason=refusal).
        if profile_id:
            redis = R.get_redis()
            # Key mirrors session_namespace so stages stay isolated.
            key = f"acp_session:{conversation_id}:{session_namespace}" if session_namespace else f"acp_session:{conversation_id}:{profile_id}"
            try:
                sid = await redis.get(key)
                if sid:
                    acp_session_id = sid.decode("utf-8") if isinstance(sid, bytes) else sid
                else:
                    # No profile-specific session yet — force a fresh session
                    # rather than reusing a potentially mismatched convo-level one.
                    acp_session_id = None
            except Exception:
                pass

        acc = {"text": "", "cancelled": False, "current_msg_id": message_id, "tool_since_split": False, "thinking": "", "plan": None, "files": [], "total_tokens": 0, "usage": None, "tool_calls": 0, "iter_capped": False}
        steps: list[dict] = []
        # Session call log: one row per model/tool call, persisted at finalize
        # for the admin 会话日志 console (kind/name/duration/tokens).
        call_collector = CallCollector(
            model_name=agent_id or "hermes",
            turn_started_at=datetime.now(tz=timezone.utc),
        )
        # Circuit breaker: cancel the ACP session once a turn emits this many
        # tool_call events. Guards against runaway ReAct loops. 0 = disabled.
        max_iterations = task.get("max_iterations") or 0
        # Late-bound ref so on_update (defined before `client` is assigned) can
        # reach the ACPClient to fire session/cancel when the cap is hit.
        client_ref: dict = {"client": None}
        # P2-3 tool risk: names of MCP servers flagged write/destructive in the
        # admin catalog. A tool_call whose title contains such a name triggers
        # a one-time confirmation (cached per-conversation in Redis).
        high_risk_names = await self._load_high_risk_server_names()
        risk_checked: set[str] = set()  # names already authorised this turn

        async def on_update(update: dict) -> None:
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                delta = (update.get("content") or {}).get("text", "")
                if delta:
                    delta = _strip_ansi(delta)
                    acc["tool_since_split"] = False
                    acc["text"] += delta
                    await R.publish_event(
                        conversation_id,
                        {"type": "token", "message_id": acc["current_msg_id"], "delta": delta},
                    )
            elif kind == "tool_call":
                acc["tool_since_split"] = True
                acc["tool_calls"] = int(acc.get("tool_calls", 0)) + 1
                # F1: capture the full tool_call payload so the frontend can
                # render tool-specific UIs (code blocks for execute_code, image
                # previews for browser_*, diffs for write_file, etc.). Keep it
                # compact — only the fields the UI cares about.
                raw_input = update.get("rawInput") or update.get("raw_input")
                if raw_input:
                    raw_input = _strip_ansi(raw_input)
                tool_kind = update.get("toolKind") or update.get("tool_kind")
                step = {
                    "title": update.get("title"),
                    "status": update.get("status"),
                    "raw_input": raw_input,
                    "tool_kind": tool_kind,
                }
                steps.append(step)
                call_collector.on_tool_call(
                    title=step["title"], status=step["status"], tool_kind=tool_kind,
                    tool_call_id=update.get("toolCallId") or update.get("tool_call_id"),
                )
                await R.publish_event(
                    conversation_id,
                    {
                        "type": "tool_call",
                        "message_id": acc["current_msg_id"],
                        "title": step["title"],
                        "status": step["status"],
                        "raw_input": raw_input,
                        "tool_kind": tool_kind,
                    },
                )
                # Circuit breaker: once a turn crosses the per-profile cap, mark
                # the turn cancelled and fire session/cancel so the ACP loop
                # unwinds instead of burning tokens until the 900s hard timeout.
                if (
                    max_iterations
                    and not acc["iter_capped"]
                    and acc["tool_calls"] >= max_iterations
                ):
                    acc["iter_capped"] = True
                    acc["cancelled"] = True
                    logger.warning(
                        "Iteration cap hit: %s tool_calls >= %s for conv=%s — cancelling session",
                        acc["tool_calls"], max_iterations, conversation_id[:8],
                    )
                    c = client_ref["client"]
                    if c is not None:
                        try:
                            await c.cancel()
                        except Exception:
                            logger.debug("session/cancel failed during iteration cap", exc_info=True)
                    await R.publish_event(
                        conversation_id,
                        {
                            "type": "iteration_warning",
                            "message_id": acc["current_msg_id"],
                            "tool_calls": acc["tool_calls"],
                            "limit": max_iterations,
                        },
                    )
                # P2-3 tool risk guard: a tool_call whose title references a
                # high-risk MCP server (write/destructive) and hasn't been
                # authorised for this conversation yet blocks the turn. ACP
                # can't intercept a single tool pre-execution, so we cancel the
                # session to stop further actions and surface the block to the
                # user — they can re-run after authorising via the UI.
                if high_risk_names and not acc["cancelled"]:
                    title = (step.get("title") or "")
                    hit = next((n for n in high_risk_names if n and n in title), None)
                    if hit and hit not in risk_checked:
                        authorised = await self._is_tool_authorised(conversation_id, hit)
                        if authorised:
                            risk_checked.add(hit)
                        else:
                            acc["cancelled"] = True
                            acc["risk_blocked"] = hit
                            c = client_ref["client"]
                            if c is not None:
                                try:
                                    await c.cancel()
                                except Exception:
                                    logger.debug("session/cancel failed during risk block", exc_info=True)
                            await R.publish_event(
                                conversation_id,
                                {
                                    "type": "tool_blocked",
                                    "message_id": acc["current_msg_id"],
                                    "tool": hit,
                                    "title": title,
                                },
                            )
            elif kind in ("agent_thought_chunk", "agent_thought"):
                # ACP v1 spec uses "agent_thought_chunk"; keep "agent_thought" as
                # a fallback for older hermes-agent versions that may emit it.
                delta = (update.get("content") or {}).get("text", "") or update.get("delta", "")
                if delta:
                    delta = _strip_ansi(delta)
                    acc["thinking"] += delta
                    await R.publish_event(conversation_id, {
                        "type": "thought",
                        "message_id": acc["current_msg_id"],
                        "delta": delta,
                    })
            elif kind == "plan":
                raw = update.get("entries") or update.get("plan") or []
                if isinstance(raw, list) and raw:
                    acc["plan"] = [{"content": _strip_ansi(e.get("content", "")), "status": e.get("status", "pending"), "priority": e.get("priority", "medium")} for e in raw if isinstance(e, dict)]
                    await R.publish_event(conversation_id, {
                        "type": "plan",
                        "message_id": acc["current_msg_id"],
                        "entries": [
                            {
                                "content": _strip_ansi(e.get("content", "")),
                                "status": e.get("status", "pending"),
                                "priority": e.get("priority", "medium"),
                            }
                            for e in raw if isinstance(e, dict)
                        ],
                    })
            elif kind == "usage":
                input_tokens = update.get("input_tokens", 0)
                output_tokens = update.get("output_tokens", 0)
                total = input_tokens + output_tokens
                acc["total_tokens"] = total
                if total > 0:
                    call_collector.on_usage(input_tokens=input_tokens, output_tokens=output_tokens)
                # Persist usage in acc for _finalize to write to DB.
                acc["usage"] = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }

                # Warn when approaching the context limit so the UI can show a
                # heads-up and the next turn may trigger compression.
                context_limit = 256_000  # TODO: read from profile config
                if total > context_limit * 0.8:
                    logger.warning(
                        "Context at %s tokens (%.0f%% of %s) for %s",
                        total, total / context_limit * 100, context_limit,
                        conversation_id[:8],
                    )
                    await R.publish_event(
                        conversation_id,
                        {
                            "type": "compression_warning",
                            "message_id": acc["current_msg_id"],
                            "tokens": total,
                            "limit": context_limit,
                        },
                    )

                await R.publish_event(
                    conversation_id,
                    {
                        "type": "usage",
                        "message_id": acc["current_msg_id"],
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                )
            elif kind == "session_info" or kind == "session_info_update":
                new_title = update.get("title")
                if new_title:
                    t = asyncio.create_task(self._update_conv_title(conversation_id, new_title))
                    self._bg_tasks.add(t)
                    t.add_done_callback(self._bg_tasks.discard)
                    await R.publish_event(conversation_id, {"type": "session_info", "title": new_title})
            elif kind == "available_commands_update":
                # F2: agent advertises its slash commands. Cache per-conversation
                # in Redis (7d) and forward to the frontend for the command palette.
                commands = update.get("commands") or []
                if commands:
                    try:
                        redis = R.get_redis()
                        await redis.set(
                            f"acp_commands:{conversation_id}",
                            __import__("json").dumps(commands, ensure_ascii=False),
                            ex=3600 * 24 * 7,
                        )
                    except Exception:
                        pass
                    await R.publish_event(conversation_id, {
                        "type": "commands_update",
                        "message_id": acc["current_msg_id"],
                        "commands": commands,
                    })
            elif kind == "user_message_chunk":
                # F2: agent echoes the user's input (chunked). Forward so the
                # frontend can display what the agent received.
                delta = (update.get("content") or {}).get("text", "")
                if delta:
                    await R.publish_event(conversation_id, {
                        "type": "user_token",
                        "message_id": acc["current_msg_id"],
                        "delta": delta,
                    })
            elif kind == "usage_update":
                size = update.get("size", 0)
                used = update.get("used", 0)
                # Merge context size/used into the persisted usage dict.
                if acc["usage"]:
                    acc["usage"]["context_size"] = size
                    acc["usage"]["context_used"] = used
                else:
                    acc["usage"] = {"input_tokens": 0, "output_tokens": 0, "context_size": size, "context_used": used}
                # The hermes agent emits usage_update (context pressure) but
                # not `usage` (token detail) — count each update as one model
                # call for the session call log, estimating tokens 80/20 from
                # context_used (same split _finalize uses for tokens_*).
                if used and used > 0:
                    est_in = int(used * 0.8)
                    call_collector.on_usage(input_tokens=est_in, output_tokens=used - est_in)
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
            # Cancel check: standalone, runs for ALL event types.
            if not acc["cancelled"] and await R.is_cancelled(conversation_id):
                acc["cancelled"] = True
                try:
                    await client.cancel()
                except Exception:  # noqa: BLE001
                    pass
            elif kind in ("tool_call_begin", "tool_call_end"):
                title = update.get("title", "")
                status = "running" if kind == "tool_call_begin" else (update.get("status") or "completed")
                sub_kind = update.get("toolKind") or update.get("tool_kind") or ""
                raw_input = update.get("rawInput") or update.get("raw_input")
                existing = next((s for s in steps if s.get("title") == title and s.get("status") == "running"), None)
                if existing:
                    existing["status"] = status
                    if raw_input:
                        existing["raw_input"] = raw_input
                    if sub_kind:
                        existing["tool_kind"] = sub_kind
                else:
                    steps.append({"title": title, "status": status, "raw_input": raw_input, "tool_kind": sub_kind})
                    # P5: cap the steps list so a very long tool-heavy turn
                    # doesn't bloat the persisted message JSON unboundedly.
                    if len(steps) > 200:
                        del steps[: len(steps) - 200]
                if kind == "tool_call_begin":
                    acc["tool_calls"] = int(acc.get("tool_calls", 0)) + 1
                call_collector.on_tool_call(
                    title=title, status=status, tool_kind=sub_kind or None,
                    tool_call_id=update.get("toolCallId") or update.get("tool_call_id"),
                )
                await R.publish_event(conversation_id, {
                    "type": "tool_call",
                    "message_id": acc["current_msg_id"],
                    "title": title,
                    "status": status,
                    "raw_input": raw_input,
                    "tool_kind": sub_kind,
                })
            elif kind == "artifact":
                artifact = update.get("artifact") or update
                a_type = artifact.get("type", "file")
                a_name = artifact.get("name") or artifact.get("title") or f"artifact_{len(acc['files'])}"
                a_content = artifact.get("content") or artifact.get("text") or ""
                if a_content and a_type in ("file", "text", "code"):
                    a_content = _strip_ansi(a_content)
                    try:
                        f = await storage.save_file(
                            uuid.UUID(conversation_id), a_name, a_content, agent_id,
                            uuid.UUID(acc["current_msg_id"]),
                        )
                        acc["files"].append({"id": str(f.id), "name": f.name, "kind": f.kind})
                        await R.publish_event(conversation_id, {
                            "type": "file",
                            "message_id": acc["current_msg_id"],
                            "file_id": str(f.id),
                            "name": f.name,
                            "kind": f.kind,
                            "version": f.current_version,
                        })
                    except Exception:
                        logger.debug("artifact save failed", exc_info=True)
            elif kind in ("elicitation", "elicitation/create"):
                req_id = update.get("request_id") or update.get("id") or str(uuid.uuid4())
                schema = update.get("schema") or update.get("form_schema") or {}
                el_question = update.get("question") or update.get("title") or "请填写以下信息"
                await R.publish_event(conversation_id, {
                    "type": "elicitation_request",
                    "message_id": acc["current_msg_id"],
                    "request_id": req_id,
                    "question": el_question,
                    "schema": schema,
                })
                t = asyncio.create_task(
                    self._wait_and_unblock_clarify_native(
                        conversation_id, req_id, sid=conversation_id,
                        message_id=acc["current_msg_id"], acc=acc,
                    )
                )
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)

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

            def _write_disk():
                os.makedirs(os.path.dirname(disk_path), exist_ok=True)
                _write_text(disk_path, content)

            await asyncio.to_thread(_write_disk)
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
            conversation_id, {"type": "start", "message_id": message_id, "agent_id": agent_id, "profile_id": task.get("profile_id")}
        )

        try:
            client, new_session = await self.pool.get(
                conversation_id, agent.command, cwd, on_update, on_fs_write,
                acp_session_id=acp_session_id, profile_dir=profile_dir,
                mcp_servers=mcp_servers,
                session_namespace=session_namespace,
                on_permission_request=self._permission_request_handler(
                    conversation_id, acc,
                ),
            )
            # Publish the client into the late-bound ref so on_update can fire
            # session/cancel when the iteration cap is reached.
            client_ref["client"] = client
            logger.info(
                "handle_single: conv=%s msg=%s ns=%s client_pid=%s new_session=%s",
                conversation_id[:8], message_id[:8], session_namespace,
                client._proc.pid if client._proc else "None", new_session,
            )
            # Start filesystem watcher for MCP write_file tools that bypass ACP.
            watcher_key = f"{conversation_id}:{session_namespace}" if session_namespace else conversation_id
            watcher = self._watchers.get(watcher_key)
            if watcher:
                watcher.stop()
            watcher = WorkspaceWatcher(
                conversation_id,
                cwd,
                agent_id,
                acc["current_msg_id"],
                get_current_msg_id=lambda: acc["current_msg_id"],
                publish_event=lambda ev: R.publish_event(conversation_id, ev),
            )
            watcher.start()
            # Scan for files written before watcher started (MCP may write
            # during prompt while the watcher wasn't active yet).
            await watcher.scan_existing()
            self._watchers[watcher_key] = watcher
            if new_session:
                await self._set_session_id(conversation_id, new_session, profile_id=profile_id, stage=stage)
                # No manual approval UI anymore — conversations without an explicit
                # mode (the vast majority) default to full-auto (dont_ask) rather
                # than the ACP agent's own default (typically "ask").
                effective_mode = session_mode or "dont_ask"
                try:
                    await client.set_session_mode(new_session, effective_mode)
                    logger.info("Applied session_mode=%s to new session %s", effective_mode, new_session[:8])
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
                # Cancel propagation: the agent may be in a long silent
                # generation (no session/update events at all), so the cancel
                # flag set by the API would otherwise never reach the ACP
                # client — the turn would complete and bill normally even
                # though the user pressed "stop". Poll the flag on the same
                # 1s cadence and fire session/cancel.
                if not acc["cancelled"] and await R.is_cancelled(conversation_id):
                    acc["cancelled"] = True
                    try:
                        await client.cancel()
                    except Exception:  # noqa: BLE001
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
            await self.pool.drop(conversation_id, session_namespace=session_namespace)
            await self._fail(conversation_id, acc["current_msg_id"], f"响应超时：{exc}",
                             calls=call_collector.records(),
                             thinking=acc.get("thinking") or "", plan=acc.get("plan"),
                             files=acc.get("files"), clarifies=acc.get("clarifies"))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("prompt failed")
            await self.pool.drop(conversation_id, session_namespace=session_namespace)
            # P7: map exception classes to readable messages instead of
            # exposing bare class names like "ACPError".
            await self._fail(
                conversation_id, acc["current_msg_id"], _friendly_error(exc),
                calls=call_collector.records(),
                thinking=acc.get("thinking") or "", plan=acc.get("plan"),
                files=acc.get("files"), clarifies=acc.get("clarifies"),
            )
            return

        if acc["cancelled"]:
            status = "cancelled"
        elif not acc["text"].strip():
            # Agent returned empty text. Distinguish root causes so the user
            # doesn't get the misleading "context too long" message for a
            # 4-message conversation that is obviously not over the limit.
            # stop_reason may be unset if prompt() raised; default to empty.
            _stop_reason = locals().get("stop_reason", "")
            logger.warning(
                "Agent returned empty text for %s (stop_reason=%s total_tokens=%s)",
                conversation_id[:8], _stop_reason, acc.get("total_tokens", 0),
            )

            fail_reason: str
            if _stop_reason in ("context_overflow", "length"):
                fail_reason = "上下文超出模型限制（已自动触发压缩，请重试）"
            elif _stop_reason == "content_filter":
                fail_reason = "内容被安全过滤（触发模型内容安全策略，请调整提问方式）"
            elif acc.get("total_tokens", 0) > 200_000:
                fail_reason = f"上下文接近上限（已用 {acc['total_tokens']} tokens，建议开启新会话）"
            else:
                # Most likely an API-layer problem, not context length.
                fail_reason = "模型未返回有效内容（可能是服务临时故障，请稍后重试；如持续出现请检查模型配置）"

            await self._fail(conversation_id, acc["current_msg_id"], fail_reason,
                             calls=call_collector.records(),
                             thinking=acc.get("thinking") or "", plan=acc.get("plan"),
                             files=acc.get("files"), clarifies=acc.get("clarifies"))
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
            acc.get("clarifies"), acc.get("usage"), call_collector.records(),
        )
        if status == "complete" and matched_skill_ids:
            await self._record_skill_firings(
                conversation_id, acc["current_msg_id"], matched_skill_ids, skill_firing_excerpt,
            )
        # P2-4: record profile firings for prompt evolution. Only complete
        # turns with a bound profile_id contribute to the eval corpus.
        if status == "complete" and profile_id:
            await self._record_profile_firing(
                conversation_id, acc["current_msg_id"], profile_id, skill_firing_excerpt,
            )
        # Auto-evolution ("Self-improvement review"): after a completed turn,
        # check whether the involved skills / profile qualify for an automatic
        # evolution run (sample threshold + cooldown). Best-effort — a failure
        # here must never break the turn completion path.
        if status == "complete":
            try:
                await self._maybe_trigger_auto_evolution(matched_skill_ids, profile_id)
            except Exception:  # noqa: BLE001
                logger.debug("auto-evolution trigger failed", exc_info=True)
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

    def _permission_request_handler(
        self, conversation_id: str, acc: dict,
    ):
        """Build the ACP request_permission callback for this turn.

        Returns an async callable (path, tool_call) -> bool. Workspace edits
        are auto-approved inside acp_client; anything else surfaces an SSE
        confirmation modal (允许/拒绝) and waits for the user — fail-closed:
        timeout/cancel/error all deny. The callback blocks the agent's tool
        call until answered (that's the point of the permission gate).
        """
        async def _handle(path: str, tool_call: dict) -> bool:
            import uuid as _uuid
            request_id = str(_uuid.uuid4())
            question = f"Agent 请求写入文件 {path}"
            req_payload = {
                "id": request_id,
                "conversation_id": conversation_id,
                "message_id": acc.get("current_msg_id"),
                "question": question,
                "questions": [{
                    "question": question,
                    "options": ["允许", "拒绝"],
                    "allow_free_text": False,
                }],
                "options": ["允许", "拒绝"],
                "permission": True,
                "path": path,
            }
            try:
                await R.publish_event(
                    conversation_id,
                    {
                        "type": "confirmation_request",
                        "message_id": acc.get("current_msg_id"),
                        "request": req_payload,
                    },
                )
                resp = await R.wait_for_confirmation(
                    conversation_id, request_id,
                    timeout=settings.clarify_timeout_seconds, cancel_check=True,
                )
                choice = resp.get("choice", "超时")
            except Exception:
                logger.warning("Permission request failed for %s — denying", path, exc_info=True)
                return False
            logger.info("Permission request %s for %s → %s", request_id[:8], path, choice)
            return choice == "允许"

        return _handle

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

        # P2: the filename must be a bare standalone name — excluding leading
        # '#'/'//' comments (LLMs often write "# main.py" before the code) and
        # path separators, so comment lines aren't mistaken for artifacts.
        code_block_re = re.compile(
            r"```(?:(\w+)\s+)?([^\s#/\\]+\.\w+)\s*\n(.*?)```",
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
            if await asyncio.to_thread(os.path.isfile, candidate):
                try:
                    content = await asyncio.to_thread(_read_text, candidate)
                    extracted.append((filename, content))
                except Exception:  # noqa: BLE001
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
        usage: dict | None = None, calls: list[dict] | None = None,
        error: str | None = None,
    ) -> None:
        # Strip ANSI escape codes (terminal color codes) that the hermes agent
        # or its tools sometimes emit. Without this they render as invisible or
        # garbled characters in the web UI.
        text = _strip_ansi(text)
        if thinking:
            thinking = _strip_ansi(thinking)

        async with async_session_maker() as db:
            msg = await db.get(Message, uuid.UUID(message_id))
            if msg:
                content: dict = {"text": text}
                if error:
                    # Machine-readable failure detail for the UI's error branch
                    # (previously the frontend only ever saw "生成中断").
                    content["error"] = error
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
                if usage:
                    content["usage"] = usage
                    tin = usage.get("input_tokens", 0)
                    tout = usage.get("output_tokens", 0)
                    # hermes agent sends usage_update (context_size/context_used)
                    # but often not usage (input/output tokens). When both are 0
                    # but context_used is available, use it as the total token
                    # consumption estimate (split roughly 80/20 input/output).
                    if tin == 0 and tout == 0:
                        ctx_used = usage.get("context_used", 0)
                        if ctx_used and ctx_used > 0:
                            tin = int(ctx_used * 0.8)
                            tout = ctx_used - tin
                            content["usage"]["input_tokens"] = tin
                            content["usage"]["output_tokens"] = tout
                    msg.tokens_in = tin
                    msg.tokens_out = tout
                msg.content = content
                msg.status = status
                convo = await db.get(Conversation, msg.conversation_id)
                if convo:
                    convo.updated_at = datetime.now(tz=timezone.utc)
                if calls:
                    # Session call log: one row per model/tool call, so the
                    # admin 会话日志 console can show a per-call overview.
                    from app.db.models.session_log import SessionCallLog
                    db.add_all([
                        SessionCallLog(
                            conversation_id=convo.id if convo else msg.conversation_id,
                            message_id=msg.id,
                            **c,
                        )
                        for c in calls
                    ])
                await db.commit()

    async def _record_skill_firings(
        self, conversation_id: str, message_id: str, skill_ids: list[str], trigger_excerpt: str,
    ) -> None:
        """Feeds the self-evolving-skills eval-dataset builder. Only called
        for turns that reached status=='complete' — a cancelled/failed turn
        was never genuinely observed under the skill's influence and would
        pollute the eval corpus."""
        from app.db.models.skill_evolution import SkillFiring

        async with async_session_maker() as db:
            convo = await db.get(Conversation, uuid.UUID(conversation_id))
            owner_id = convo.owner_id if convo else None
            for sid in skill_ids:
                try:
                    skill_uuid = uuid.UUID(sid)
                except (ValueError, TypeError):
                    continue
                db.add(SkillFiring(
                    skill_id=skill_uuid,
                    message_id=uuid.UUID(message_id),
                    conversation_id=uuid.UUID(conversation_id),
                    owner_id=owner_id,
                    trigger_query_excerpt=trigger_excerpt,
                ))
            await db.commit()

    async def _record_profile_firing(
        self, conversation_id: str, message_id: str, profile_id: str, trigger_excerpt: str,
    ) -> None:
        """P2-4: feeds the profile-prompt eval-dataset builder. Records each
        complete turn attributed to a Profile."""
        from app.db.models.profile_evolution import ProfileFiring
        try:
            profile_uuid = uuid.UUID(profile_id)
        except (ValueError, TypeError):
            return
        async with async_session_maker() as db:
            db.add(ProfileFiring(
                profile_id=profile_uuid,
                message_id=uuid.UUID(message_id),
                conversation_id=uuid.UUID(conversation_id),
                trigger_query_excerpt=trigger_excerpt,
            ))
            await db.commit()

    async def _maybe_trigger_auto_evolution(
        self, matched_skill_ids: list[uuid.UUID] | None, profile_id: str | None,
    ) -> None:
        """Auto-evolution: enqueue skill/profile evolution runs for entities
        that accumulated enough firing samples and are past the cooldown.

        All checks live in evolution_service (config-gated on
        evolution_auto_enabled AND skill_evolution_enabled — the LLM-free
        stub never auto-triggers). Best-effort: callers wrap this in
        try/except so the chat hot path is never blocked."""
        from app.services import evolution_service

        # Profile (the turn's bound persona) — one query, own session.
        if profile_id:
            try:
                profile_uuid = uuid.UUID(profile_id)
            except (ValueError, TypeError):
                profile_uuid = None
            if profile_uuid:
                async with async_session_maker() as db:
                    if await evolution_service.should_trigger_profile(db, profile_uuid):
                        await evolution_service.enqueue_profile_evolution(profile_uuid)

        # Skills whose content was injected into this turn.
        for sid in (matched_skill_ids or []):
            try:
                skill_uuid = uuid.UUID(str(sid))
            except (ValueError, TypeError):
                continue
            async with async_session_maker() as db:
                if await evolution_service.should_trigger_skill(db, skill_uuid):
                    await evolution_service.enqueue_skill_evolution(skill_uuid)

    async def _set_session_id(
        self, conversation_id: str, session_id: str, profile_id: str = "", stage: str = "",
    ) -> None:
        async with async_session_maker() as db:
            convo = await db.get(Conversation, uuid.UUID(conversation_id))
            if convo:
                if profile_id:
                    # Profile-specific session → Redis so different agents in a
                    # group chat don't overwrite each other's ACP session.
                    # P1-3: the key includes the stage so each stage's session
                    # (with its own tool subset) is stored separately.
                    redis = R.get_redis()
                    suffix = f":{stage}" if stage else ""
                    key = f"acp_session:{conversation_id}:{profile_id}{suffix}"
                    await redis.set(key, session_id, ex=3600 * 24 * 7)  # 7d TTL
                else:
                    convo.acp_session_id = session_id
                await db.commit()

    async def _load_high_risk_server_names(self) -> set[str]:
        """P2-3: names of MCP servers flagged write/destructive in the admin
        catalog. Read once per turn (cheap single-row system_settings lookup).
        Disabled servers are excluded — they aren't injected into the session,
        so their names must not trigger the tool-risk guard either."""
        try:
            async with async_session_maker() as db:
                from app.services import settings_service
                row = await settings_service.get(db)
                servers = (row.data or {}).get("mcp_servers", []) if row else []
                return {
                    s["name"] for s in servers
                    if s.get("risk_level") in ("write", "destructive")
                    and s.get("name")
                    and s.get("enabled", True)
                }
        except Exception:  # noqa: BLE001 — never break a turn over the risk map
            logger.debug("failed to load high-risk server names", exc_info=True)
            return set()

    async def _is_tool_authorised(self, conversation_id: str, tool_name: str) -> bool:
        """P2-3: check the per-conversation authorisation cache. Set by the
        authorise-tool API after the user confirms a high-risk tool once."""
        try:
            redis = R.get_redis()
            val = await redis.get(f"tool_auth:{conversation_id}:{tool_name}")
            return val is not None
        except Exception:
            return False

    async def _update_conv_title(self, conversation_id: str, title: str) -> None:
        from sqlalchemy import update as sa_upd
        async with async_session_maker() as db:
            await db.execute(
                sa_upd(Conversation)
                .where(Conversation.id == uuid.UUID(conversation_id))
                .values(title=title)
            )
            await db.commit()

    async def _fail(
        self, conversation_id: str, message_id: str, detail: str,
        calls: list[dict] | None = None,
        thinking: str | None = None, plan: list[dict] | None = None,
        files: list[dict] | None = None, clarifies: list[dict] | None = None,
    ) -> None:
        # Keep whatever the agent already streamed (thinking/plan/files) so a
        # failed turn still shows its reasoning in the session log. error= is
        # written to content.error so the UI can show the actual reason.
        await self._finalize(
            message_id, f"⚠ 生成失败：{detail}", "error",
            calls=calls, thinking=thinking, plan=plan, files=files,
            clarifies=clarifies, error=detail,
        )
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
