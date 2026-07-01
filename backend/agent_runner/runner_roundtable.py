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
from agent_runner.runner_clarify import pop_clarify_request, deliver_clarify_response

logger = logging.getLogger("hermes.runner")


async def handle_roundtable(task: dict, agents: dict) -> None:
    """Handle roundtable task with multiple agents.

    `task["targets"]` is a list of {"agent_id", "profile_id", "system_prompt",
    "profile_dir"} dicts — one per distinct AI participant. Profiles sharing
    an agent_id are NOT collapsed: each gets its own persona (system_prompt)
    and env (profile_dir), so the roundtable actually differs per
    participant instead of every slot answering identically.
    """
    conversation_id = task["conversation_id"]
    message_id = task["message_id"]
    targets: list[dict] = task["targets"]
    text = task["text"]

    cwd = os.path.join(settings.workspace_root, conversation_id)
    os.makedirs(cwd, exist_ok=True)

    slots = []
    for i, t in enumerate(targets):
        aid = t["agent_id"]
        a = agents.get(aid)
        slots.append({
            "agent_id": aid, "profile_id": t.get("profile_id"), "slot": i,
            "label": a.label if a else aid,
            "color": a.color if a else "#b8852a",
            "stance": a.description if a else "",
        })
    await R.publish_event(
        conversation_id, {"type": "rt_start", "message_id": message_id, "agents": slots}
    )

    async def run_one(slot: int, target: dict) -> tuple[str, str]:
        """Run one roundtable reply. Returns (text, status) where status is
        complete | timeout | error. Partial text is preserved on failure."""
        aid = target["agent_id"]
        agent = agents.get(aid) or agents.get("hermes")
        buf = {"text": ""}
        reply_status = "complete"
        env = profile_env(target.get("profile_dir") or None)
        prompt_text = text
        if target.get("system_prompt"):
            prompt_text = f"【角色设定】\n{target['system_prompt']}\n【角色设定结束】\n\n{text}"

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
            # Also write to disk so the agent can read its own output later.
            from app.core.files import confine_to_dir, safe_relative_path
            disk_path = confine_to_dir(cwd, safe_relative_path(path))
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)
            with open(disk_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            await R.publish_event(conversation_id, {
                "type": "file", "message_id": message_id, "file_id": str(f.id),
                "name": f.name, "kind": f.kind, "version": f.current_version,
            })

        client = ACPClient(
            agent.command, cwd, protocol_version=settings.acp_protocol_version,
            on_update=on_update, on_fs_write=on_fs, env=env,
        )
        try:
            await client.start()
            await client.initialize()
            session_id = await client.new_session(cwd)
            # Nobody can answer an interactive clarify modal mid-roundtable —
            # drain any clarify request the agent raises and auto-decline it
            # immediately instead of letting it hang until ACPTimeout. This is
            # a hard backstop; the prompt preamble asking agents not to call
            # clarify is advisory only and can't be trusted on its own.
            clarify_sid = session_id or conversation_id
            prompt_task = asyncio.create_task(client.prompt(prompt_text))
            while not prompt_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(prompt_task), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                try:
                    data = await pop_clarify_request(clarify_sid)
                    if data:
                        clarify_id = data.get("clarify_id") or uuid.uuid4().hex[:12]
                        logger.info(
                            "roundtable clarify auto-declined (%s): %s", aid, clarify_id,
                        )
                        await deliver_clarify_response(clarify_sid, clarify_id, "")
                except Exception:
                    logger.debug("roundtable clarify poll failed", exc_info=True)
            await prompt_task
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
        *[run_one(i, t) for i, t in enumerate(targets)], return_exceptions=True
    )
    texts = [r[0] if isinstance(r, tuple) else "（作答失败）" for r in results]
    statuses = [r[1] if isinstance(r, tuple) else "error" for r in results]

    if await R.is_cancelled(conversation_id):
        await _finalize_roundtable(message_id, targets, texts, statuses, "", "cancelled")
        await R.clear_cancel(conversation_id)
        await R.publish_event(conversation_id, {
            "type": "done", "message_id": message_id, "status": "cancelled"
        })
        return

    ok_slots = [i for i, s in enumerate(statuses) if s == "complete" and texts[i].strip()]
    if not ok_slots:
        await _finalize_roundtable(message_id, targets, texts, statuses, "", "error")
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
            f"【{targets[i]['agent_id']}】{texts[i]}" for i in ok_slots
        )
        hermes = agents.get("hermes") or agents.get(targets[0]["agent_id"])

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
            on_update=on_merge, on_fs_write=_noop, env=profile_env(None),
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

    await _finalize_roundtable(message_id, targets, texts, statuses, merged["text"], "complete")
    await R.clear_cancel(conversation_id)
    await R.publish_event(
        conversation_id, {"type": "done", "message_id": message_id, "status": "complete"}
    )


async def _finalize_roundtable(
    message_id: str, targets: list[dict], texts: list[str],
    statuses: list[str], merged: str, status: str,
) -> None:
    async with async_session_maker() as db:
        msg = await db.get(Message, uuid.UUID(message_id))
        if msg:
            msg.content = {
                "replies": [
                    {
                        "agent_id": targets[i]["agent_id"],
                        "profile_id": targets[i].get("profile_id"),
                        "text": texts[i],
                        "status": statuses[i],
                    }
                    for i in range(len(targets))
                ],
                "merged": {"text": merged, "status": status},
            }
            msg.status = status
            convo = await db.get(Conversation, msg.conversation_id)
            if convo:
                convo.updated_at = datetime.now(tz=timezone.utc)
            await db.commit()
