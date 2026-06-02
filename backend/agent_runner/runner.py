"""Agent Runner main loop — the ACP gateway.

  Redis Stream `acp:prompt`  ──►  ACP subprocess (Hermes/mock)  ──►  Redis Pub/Sub `chan:conv:{id}`

Performance: the per-token path is Redis-only (publish), never the DB. The
agent message row is written once on completion.

Stability features:
  - Singleton lock: only one runner active at a time (Redis distributed lock)
  - ACP timeouts: prompt 300s, start/init 30s — no infinite hangs
  - Stale reclaim: stuck pending messages auto-claimed after 60s
  - Graceful shutdown: SIGTERM/SIGINT handled, ACP subprocesses cleaned up
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
from agent_runner.acp_client import ACPClient, ACPError, ACPTimeout
from agent_runner.session_pool import SessionPool

logger = logging.getLogger("hermes.runner")

# ── Stability constants ──
LOCK_KEY = "hermes:runner:lock"
LOCK_TTL = 30          # seconds; must be > heartbeat interval
HEARTBEAT_INTERVAL = 10  # refresh lock every N seconds
STALE_THRESHOLD_MS = 120_000  # 2 minutes — reclaim stuck pending messages
RECLAIM_INTERVAL = 30   # check for stale messages every N seconds


class Runner:
    def __init__(self) -> None:
        self.pool = SessionPool()
        self.agents: dict[str, discovery.DiscoveredAgent] = {}
        self._shutdown = False
        self._lock_token: str | None = None

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
            if current and current.decode() == self._lock_token:
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
        """Background task: refresh lock + reclaim stale messages."""
        counter = 0
        while not self._shutdown:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self._refresh_lock()
            counter += 1
            if counter * HEARTBEAT_INTERVAL >= RECLAIM_INTERVAL:
                counter = 0
                await self._reclaim_stale()

    # ── main loop ──
    async def run(self) -> None:
        configure_logging()

        # Register signal handlers (async-safe)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))

        # Singleton check
        if not await self._acquire_lock():
            return

        await self.register_agents()
        await self.ensure_group()
        logger.info("Runner consuming %s as %s/%s",
                    settings.acp_stream, settings.acp_group, settings.acp_consumer)

        # Start background heartbeat + reclaim
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

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
                except Exception:
                    logger.exception("xreadgroup failed; backing off")
                    await asyncio.sleep(2)
                    continue

                if not resp:
                    continue
                for _stream, entries in resp:
                    for entry_id, fields in entries:
                        if self._shutdown:
                            break
                        try:
                            await self.handle(json.loads(fields["data"]))
                        except Exception:
                            logger.exception("handle failed for %s", entry_id)
                        finally:
                            try:
                                await R.get_redis().xack(
                                    settings.acp_stream, settings.acp_group, entry_id
                                )
                            except Exception:
                                logger.warning("Failed to ACK %s", entry_id)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self._release_lock()

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

        async def run_one(slot: int, aid: str) -> str:
            agent = self.agents.get(aid) or self.agents.get("hermes")
            buf = {"text": ""}

            async def on_update(update: dict) -> None:
                if update.get("sessionUpdate") == "agent_message_chunk":
                    d = (update.get("content") or {}).get("text", "")
                    if d:
                        buf["text"] += d
                        await R.publish_event(conversation_id, {
                            "type": "rt_token", "message_id": message_id, "slot": slot, "delta": d
                        })

            async def on_fs(path: str, content: str) -> None:
                f = await storage.save_file(uuid.UUID(conversation_id), path, content, aid)
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
                buf["text"] = buf["text"] or f"（{aid} 超时未响应）"
            except Exception:  # noqa: BLE001
                logger.exception("roundtable reply failed (%s)", aid)
                buf["text"] = buf["text"] or "（该助手作答失败）"
            finally:
                await client.stop()
            await R.publish_event(
                conversation_id, {"type": "rt_reply_done", "message_id": message_id, "slot": slot}
            )
            return buf["text"]

        results = await asyncio.gather(
            *[run_one(i, aid) for i, aid in enumerate(agent_ids)], return_exceptions=True
        )
        texts = [r if isinstance(r, str) else "（作答失败）" for r in results]

        if await R.is_cancelled(conversation_id):
            await self._finalize_roundtable(message_id, agent_ids, texts, "", "cancelled")
            await R.clear_cancel(conversation_id)
            await R.publish_event(conversation_id, {
                "type": "done", "message_id": message_id, "status": "cancelled"
            })
            return

        # ── merge via Hermes ──
        await R.publish_event(conversation_id, {"type": "merge_start", "message_id": message_id})
        merge_prompt = "请综合以下各助手的观点，给出一致结论与下一步：\n\n" + "\n\n".join(
            f"【{agent_ids[i]}】{texts[i]}" for i in range(len(agent_ids))
        )
        hermes = self.agents.get("hermes") or self.agents.get(agent_ids[0])
        merged = {"text": ""}

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

        await self._finalize_roundtable(message_id, agent_ids, texts, merged["text"], "complete")
        await R.clear_cancel(conversation_id)
        await R.publish_event(
            conversation_id, {"type": "done", "message_id": message_id, "status": "complete"}
        )

    async def _finalize_roundtable(
        self, message_id: str, agent_ids: list[str], texts: list[str], merged: str, status: str
    ) -> None:
        async with async_session_maker() as db:
            msg = await db.get(Message, uuid.UUID(message_id))
            if msg:
                msg.content = {
                    "replies": [
                        {"agent_id": agent_ids[i], "text": texts[i], "status": "complete"}
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

        agent = self.agents.get(agent_id) or self.agents.get("hermes")
        if agent is None:
            await self._fail(conversation_id, message_id, "没有可用的 agent")
            return

        cwd = os.path.join(settings.workspace_root, conversation_id)
        os.makedirs(cwd, exist_ok=True)

        acc = {"text": "", "cancelled": False}

        async def on_update(update: dict) -> None:
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                delta = (update.get("content") or {}).get("text", "")
                if delta:
                    acc["text"] += delta
                    await R.publish_event(
                        conversation_id,
                        {"type": "token", "message_id": message_id, "delta": delta},
                    )
            elif kind == "tool_call":
                await R.publish_event(
                    conversation_id,
                    {
                        "type": "tool_call",
                        "message_id": message_id,
                        "title": update.get("title"),
                        "status": update.get("status"),
                    },
                )
            # Cooperative cancellation between chunks.
            if not acc["cancelled"] and await R.is_cancelled(conversation_id):
                acc["cancelled"] = True
                try:
                    await client.cancel()
                except Exception:  # noqa: BLE001
                    pass

        async def on_fs_write(path: str, content: str) -> None:
            f = await storage.save_file(
                uuid.UUID(conversation_id), path, content, agent_id
            )
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

        await R.publish_event(
            conversation_id, {"type": "start", "message_id": message_id}
        )

        try:
            client, new_session = await self.pool.get(
                conversation_id, agent.command, cwd, on_update, on_fs_write
            )
            if new_session:
                await self._set_session_id(conversation_id, new_session)
            stop_reason = await client.prompt(text)
        except ACPTimeout as exc:
            logger.error("prompt timed out for %s: %s", conversation_id, exc)
            await self.pool.drop(conversation_id)
            await self._fail(conversation_id, message_id, f"响应超时：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("prompt failed")
            await self.pool.drop(conversation_id)
            await self._fail(conversation_id, message_id, f"{exc.__class__.__name__}")
            return

        status = "cancelled" if acc["cancelled"] else "complete"
        await self._finalize(message_id, acc["text"], status)
        await R.clear_cancel(conversation_id)
        await R.publish_event(
            conversation_id,
            {
                "type": "done",
                "message_id": message_id,
                "stop_reason": stop_reason,
                "status": status,
            },
        )

    # ── DB writes (off the hot path) ──
    async def _finalize(self, message_id: str, text: str, status: str) -> None:
        async with async_session_maker() as db:
            msg = await db.get(Message, uuid.UUID(message_id))
            if msg:
                msg.content = {"text": text}
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
