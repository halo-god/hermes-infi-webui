"""P2-1 chain handoff: N agents in sequence, each conclusion prepended to the
next's prompt. No merge step — the last agent's output is the final answer.

Mirrors runner_roundtable's run_one() template but runs targets sequentially
(for ... await) instead of asyncio.gather, and threads each output into the
next prompt. SSE events: chain_start / chain_step_token / chain_step_done.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.conversation import Conversation, Message
from agent_runner import storage
from agent_runner.acp_client import ACPTimeout
from agent_runner.acp_persona import (
    make_persona_client,
    run_prompt_with_clarify_guard,
    start_persona_session,
    wrap_persona_prompt,
)

logger = logging.getLogger("hermes.runner")


async def handle_chain(task: dict, agents: dict) -> None:
    """Handle a chain task: sequential relay across ordered targets."""
    conversation_id = task["conversation_id"]
    message_id = task["message_id"]
    targets: list[dict] = task["targets"]
    text = task["text"]
    attachment_blocks: list[dict] = task.get("content_blocks") or []

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
        })
    await R.publish_event(
        conversation_id, {"type": "chain_start", "message_id": message_id, "agents": slots}
    )

    texts: list[str] = [""] * len(targets)
    statuses: list[str] = ["pending"] * len(targets)
    carried_text = text  # the prompt fed to the next agent; starts as user text

    for i, target in enumerate(targets):
        # A cancelled chain (e.g. user hit stop) short-circuits remaining steps.
        if await R.is_cancelled(conversation_id):
            statuses[i] = "cancelled"
            continue

        aid = target["agent_id"]
        agent = agents.get(aid) or agents.get("hermes")
        buf = {"text": ""}
        # The prompt this agent sees: its persona + the carried context (user
        # text for step 0, previous agent's conclusion for step 1+).
        step_prompt = wrap_persona_prompt(carried_text, target.get("system_prompt"))
        if i > 0:
            step_prompt = (
                f"【上一环节（{targets[i - 1]['agent_id']}）的结论】\n{carried_text}\n"
                f"【结论结束】\n\n请基于以上结论继续完成任务。"
            )
            step_prompt = wrap_persona_prompt(step_prompt, target.get("system_prompt"))
        prompt_content: str | list[dict] = (
            [{"type": "text", "text": step_prompt}, *attachment_blocks]
            if attachment_blocks and i == 0 else step_prompt
        )

        async def on_update(update: dict, _slot=i) -> None:
            if update.get("sessionUpdate") == "agent_message_chunk":
                d = (update.get("content") or {}).get("text", "")
                if d:
                    buf["text"] += d
                    await R.publish_event(conversation_id, {
                        "type": "chain_step_token", "message_id": message_id, "slot": _slot, "delta": d
                    })

        async def on_fs(path: str, content: str, _aid=aid) -> None:
            f = await storage.save_file(uuid.UUID(conversation_id), path, content, _aid, uuid.UUID(message_id))
            from app.core.files import confine_to_dir, safe_relative_path
            disk_path = confine_to_dir(cwd, safe_relative_path(path))
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)
            with open(disk_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            await R.publish_event(conversation_id, {
                "type": "file", "message_id": message_id, "file_id": str(f.id),
                "name": f.name, "kind": f.kind, "version": f.current_version,
            })

        client = make_persona_client(
            agent.command, cwd, on_update=on_update, on_fs_write=on_fs,
            profile_dir=target.get("profile_dir"),
        )
        reply_status = "complete"
        try:
            session_id = await start_persona_session(client, cwd, target.get("mcp_servers"))
            clarify_sid = session_id or conversation_id
            await run_prompt_with_clarify_guard(client, clarify_sid, prompt_content, aid)
        except ACPTimeout:
            logger.error("chain step %s timeout (%s)", i, aid)
            reply_status = "timeout"
            buf["text"] = buf["text"] or f"（{aid} 超时未响应）"
        except Exception:  # noqa: BLE001
            logger.exception("chain step %s failed (%s)", i, aid)
            reply_status = "error"
            buf["text"] = buf["text"] or "（该环节失败）"
        finally:
            await client.stop()

        texts[i] = buf["text"]
        statuses[i] = reply_status
        # Carry this agent's conclusion to the next step.
        carried_text = buf["text"]
        await R.publish_event(conversation_id, {
            "type": "chain_step_done", "message_id": message_id, "slot": i, "status": reply_status,
        })

    # Final status: complete if the last step completed; otherwise error/cancelled.
    final_status = statuses[-1] if statuses else "error"
    if final_status not in ("complete",):
        # On failure, mark overall as error but keep partial outputs.
        overall = "error" if final_status != "cancelled" else "cancelled"
    else:
        overall = "complete"

    await _finalize_chain(message_id, targets, texts, statuses, overall)
    await R.clear_cancel(conversation_id)
    await R.publish_event(conversation_id, {
        "type": "done", "message_id": message_id, "status": overall, "text": texts[-1] if texts else "",
    })


async def _finalize_chain(
    message_id: str, targets: list[dict], texts: list[str],
    statuses: list[str], status: str,
) -> None:
    async with async_session_maker() as db:
        msg = await db.get(Message, uuid.UUID(message_id))
        if msg:
            msg.content = {
                "steps": [
                    {
                        "agent_id": targets[i]["agent_id"],
                        "profile_id": targets[i].get("profile_id"),
                        "text": texts[i],
                        "status": statuses[i],
                    }
                    for i in range(len(targets))
                ],
            }
            msg.status = status
            convo = await db.get(Conversation, msg.conversation_id)
            if convo:
                convo.updated_at = datetime.now(tz=timezone.utc)
            await db.commit()
