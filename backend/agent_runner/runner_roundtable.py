"""Roundtable: N agents in parallel, then Hermes merge."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.conversation import Conversation, Message
from agent_runner import storage
from agent_runner.acp_client import ACPClient, ACPTimeout, profile_env

logger = logging.getLogger("hermes.runner")


async def handle_roundtable(task: dict, agents: dict) -> None:
    """Handle roundtable task with multiple agents."""
    conversation_id = task["conversation_id"]
    message_id = task["message_id"]
    agent_ids: list[str] = task["agents"]
    text = task["text"]
    rt_env = profile_env(task.get("profile_dir") or None)

    cwd = os.path.join(settings.workspace_root, conversation_id)
    os.makedirs(cwd, exist_ok=True)

    slots = []
    for i, aid in enumerate(agent_ids):
        a = agents.get(aid)
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
        agent = agents.get(aid) or agents.get("hermes")
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
            on_update=on_update, on_fs_write=on_fs, env=rt_env,
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
        await _finalize_roundtable(message_id, agent_ids, texts, statuses, "", "cancelled")
        await R.clear_cancel(conversation_id)
        await R.publish_event(conversation_id, {
            "type": "done", "message_id": message_id, "status": "cancelled"
        })
        return

    ok_slots = [i for i, s in enumerate(statuses) if s == "complete" and texts[i].strip()]
    if not ok_slots:
        await _finalize_roundtable(message_id, agent_ids, texts, statuses, "", "error")
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
        merged["text"] = texts[ok_slots[0]]
        await R.publish_event(conversation_id, {
            "type": "merge_token", "message_id": message_id, "delta": merged["text"]
        })
    else:
        merge_prompt = "请综合以下各助手的观点，给出一致结论与下一步：\n\n" + "\n\n".join(
            f"【{agent_ids[i]}】{texts[i]}" for i in ok_slots
        )
        hermes = agents.get("hermes") or agents.get(agent_ids[0])

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
            on_update=on_merge, on_fs_write=_noop, env=rt_env,
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

    await _finalize_roundtable(message_id, agent_ids, texts, statuses, merged["text"], "complete")
    await R.clear_cancel(conversation_id)
    await R.publish_event(
        conversation_id, {"type": "done", "message_id": message_id, "status": "complete"}
    )


async def _finalize_roundtable(
    message_id: str, agent_ids: list[str], texts: list[str],
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
