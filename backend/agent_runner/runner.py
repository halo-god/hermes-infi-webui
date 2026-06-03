"""Agent Runner main loop — the ACP gateway.

  Redis Stream `acp:prompt`  ──►  ACP subprocess (Hermes/mock)  ──►  Redis Pub/Sub `chan:conv:{id}`

Performance: the per-token path is Redis-only (publish), never the DB. The
agent message row is written once on completion.

Stability features:
  - Singleton lock: only one runner active at a time (Redis distributed lock)
  - ACP timeouts: prompt 300s, start/init 30s — no infinite hangs
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
        logger.info("Runner consuming %s as %s/%s (max_concurrent=%d)",
                    settings.acp_stream, settings.acp_group, settings.acp_consumer, MAX_CONCURRENT)

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
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            # Wait for all active tasks to finish (with timeout)
            if self._active_tasks:
                logger.info("Waiting for %d active task(s) to finish...", len(self._active_tasks))
                await asyncio.wait(self._active_tasks, timeout=60)
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
            elif kind == "confirmation_request":
                request_id = update.get("request_id", str(uuid.uuid4()))
                await R.publish_event(
                    conversation_id,
                    {
                        "type": "confirmation_request",
                        "message_id": message_id,
                        "request": {
                            "id": request_id,
                            "conversation_id": conversation_id,
                            "message_id": message_id,
                            "question": update.get("question", "需要你的确认"),
                            "options": update.get("options", ["继续", "跳过"]),
                        },
                    },
                )
                async def _notify_response(cid: str, rid: str) -> None:
                    try:
                        resp = await R.wait_for_confirmation(cid, rid, timeout=300)
                        await R.publish_event(
                            cid,
                            {"type": "confirmation_response", "request_id": rid, "choice": resp.get("choice", "")},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                asyncio.create_task(_notify_response(conversation_id, request_id))
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
            logger.info(
                "handle_single: conv=%s msg=%s client_pid=%s new_session=%s",
                conversation_id[:8], message_id[:8],
                client._proc.pid if client._proc else "None", new_session,
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
        # ── Fallback: extract files from text if agent didn't use fs/write_text_file ──
        if status == "complete" and acc["text"]:
            await self._extract_and_save_files(
                conversation_id, message_id, agent_id, acc["text"]
            )

        # ── Clarification detection: if agent response is a question with options,
        #    inject a confirmation_request so the frontend shows a choice dialog. ──
        if status == "complete" and acc["text"]:
            detected = self._detect_clarification(acc["text"])
            if detected:
                request_id = str(uuid.uuid4())
                question, options_or_questions = detected
                # Check if multi-question (list of dicts) or single question (list of str)
                is_multi = (
                    isinstance(options_or_questions, list)
                    and options_or_questions
                    and isinstance(options_or_questions[0], dict)
                )
                request_payload = {
                    "id": request_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "question": question,
                }
                if is_multi:
                    request_payload["questions"] = options_or_questions
                    request_payload["options"] = []  # legacy compat
                else:
                    request_payload["options"] = options_or_questions
                await R.publish_event(
                    conversation_id,
                    {
                        "type": "confirmation_request",
                        "message_id": message_id,
                        "request": request_payload,
                    },
                )
                # Don't await response here — the user's reply will come through
                # the normal message flow with conversation context.

        await self._finalize(message_id, acc["text"], status)
        await R.clear_cancel(conversation_id)
        await R.publish_event(
            conversation_id,
            {
                "type": "done",
                "message_id": message_id,
                "stop_reason": stop_reason,
                "status": status,
                "text": acc["text"],
            },
        )

    # ── Clarification detection: scan for questions with options ──
    @staticmethod
    def _detect_clarification(text: str) -> tuple[str, list[str] | list[dict]] | None:
        """Detect if the agent response is a clarification question with options.

        Returns (question, options_or_questions) if detected, None otherwise.
        Supports:
        - ### template: "### Q1\n1. A\n2. B\n\n### Q2\n1. C\n2. D"
        - Numbered with sub-bullets: "1. **Q1**\n   - A\n   - B\n2. **Q2**"
        - Legacy numbered lists: "1. A\n2. B"
        - "A还是B" pattern
        """
        import re

        text = text.strip()

        # Pattern 0: Multi-question template — "### xxx" sections
        h3_sections = re.findall(r"###\s*(.+?)(?=\n|$)(.*?)(?=###|\Z)", text, re.DOTALL)
        if h3_sections:
            questions = []
            for title, body in h3_sections:
                title = title.strip()
                numbered = re.findall(r"(?:^|\n)\s*\d+[.、)）]\s*(.+?)(?=\n|$)", body)
                if numbered:
                    options = []
                    for opt in numbered:
                        cleaned = opt.strip().rstrip("？?。. ")
                        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
                        cleaned = re.sub(r"[（(][^）)]*[）)]", "", cleaned).strip()
                        if cleaned:
                            options.append(cleaned)
                    if options:
                        questions.append({"question": title, "options": options})
            if len(questions) >= 1:
                marker = re.search(r"###", text)
                preamble = text[: marker.start()].strip() if marker else ""
                preamble = re.sub(r"##\s*确认\s*", "", preamble)
                preamble = re.sub(r"需要确认以下信息[：:]\s*", "", preamble)
                preamble = preamble.strip()
                return preamble or "请选择", questions

        # Pattern 1: Numbered items with sub-bullets (agent's typical format)
        # "1. **项目类型** — 什么方向？\n   - Web全栈\n   - CLI工具\n2. **技术栈**"
        numbered_blocks = re.finditer(
            r"(?:^|\n)\s*(\d+)[.、)）]\s*(.+?)(?=\n\s*\d+[.、)）]|\Z)",
            text,
            re.DOTALL,
        )
        questions_from_blocks = []
        for match in numbered_blocks:
            block_text = match.group(2).strip()
            # Extract question: first line (before any sub-bullets)
            lines = block_text.split("\n")
            question_line = lines[0].strip()
            # Clean markdown
            question_line = re.sub(r"\*\*([^*]+)\*\*", r"\1", question_line)
            question_line = question_line.rstrip("？?。:：")
            # Extract sub-bullets as options
            options = []
            for line in lines[1:]:
                bullet_match = re.match(r"\s*[-*•]\s*(.+)", line)
                if bullet_match:
                    opt = bullet_match.group(1).strip()
                    opt = re.sub(r"\*\*([^*]+)\*\*", r"\1", opt)
                    opt = re.sub(r"[（(][^）)]*[）)]", "", opt).strip()
                    opt = opt.rstrip("？?。. ")
                    if opt:
                        options.append(opt)
            if question_line:
                questions_from_blocks.append({
                    "question": question_line,
                    "options": options if options else [],
                })
        if len(questions_from_blocks) >= 2:
            # At least 2 questions to trigger multi-step
            preamble = re.search(r"^(.*?)(?=\n\s*\d+[.、)）])", text, re.DOTALL)
            preamble_text = preamble.group(1).strip() if preamble else ""
            return preamble_text or "请选择", questions_from_blocks

        # Pattern 2: Single question template — "## 确认" or "需要确认以下信息"
        template_match = re.search(
            r"(?:##\s*确认|需要确认以下信息)[：:\s]*(.*)", text, re.DOTALL
        )
        if template_match:
            block = template_match.group(1)
            numbered = re.findall(r"(?:^|\n)\s*\d+[.、)）]\s*(.+?)(?=\n|$)", block)
            if len(numbered) >= 2:
                marker = re.search(r"(?:##\s*确认|需要确认以下信息)", text)
                question = text[: marker.start()].strip() if marker else ""
                options = []
                for opt in numbered:
                    cleaned = opt.strip().rstrip("？?。. ")
                    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
                    cleaned = re.sub(r"[（(][^）)]*[）)]", "", cleaned).strip()
                    if cleaned:
                        options.append(cleaned)
                if len(options) >= 2:
                    return question or "请选择", options

        # Pattern 3: Simple numbered list (legacy)
        numbered = re.findall(r"(?:^|\n)\s*\d+[.、)）]\s*(.+?)(?=\n|$)", text)
        if len(numbered) >= 2:
            first_num = re.search(r"(?:^|\n)\s*\d+[.、)）]", text)
            question = text[: first_num.start()].strip() if first_num else text
            options = []
            for opt in numbered:
                cleaned = opt.rstrip("？?。. ").strip()
                cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
                cleaned = re.sub(r"[（(][^）)]*[）)]", "", cleaned).strip()
                if "：" in cleaned:
                    cleaned = cleaned.split("：")[0].strip()
                elif ":" in cleaned:
                    cleaned = cleaned.split(":")[0].strip()
                if cleaned:
                    options.append(cleaned)
            if len(options) >= 2:
                return question or "请选择", options

        # Pattern 4: "A还是B"
        haisi = re.search(r"(.+?)还是(.+?)[？?]?\s*$", text)
        if haisi:
            opts = [haisi.group(1).strip(), haisi.group(2).strip()]
            question = text[: haisi.start()].strip()
            return question or "请选择", opts

        return None

    # ── Fallback: extract files from AI text response ──
    async def _extract_and_save_files(
        self, conversation_id: str, message_id: str, agent_id: str, text: str
    ) -> None:
        """Parse AI response text for file artifacts and save them to the workspace.

        Catches two patterns:
        1. Fenced code blocks with a filename hint (```python filename.py or ```filename.txt)
        2. Explicit file path mentions (路径: ~/Downloads/xxx, 文件已生成 + path)

        Files already saved via fs/write_text_file are skipped (dedup by name).
        """
        import re

        from sqlalchemy import select

        from app.db.models.workspace import WorkspaceFile

        cid = uuid.UUID(conversation_id)
        saved_names: set[str] = set()

        # Check what's already in the workspace (from fs/write_text_file)
        async with async_session_maker() as db:
            res = await db.execute(
                select(WorkspaceFile.name).where(WorkspaceFile.conversation_id == cid)
            )
            saved_names = {row[0] for row in res.all()}

        extracted: list[tuple[str, str]] = []  # (filename, content)

        # Pattern 1: fenced code blocks with filename
        # Matches: ```python filename.py\n...\n``` or ```filename.txt\n...\n```
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
                f = await storage.save_file(cid, filename, content, agent_id)
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
