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
from agent_runner.acp_client import auto_deny_permission
from agent_runner.acp_persona import (
    make_persona_client,
    run_prompt_with_clarify_guard,
    start_persona_session,
    wrap_persona_prompt,
)
from agent_runner.call_log import CallCollector
from agent_runner.metrics import TOOL_GOVERNANCE
from agent_runner.runner import is_tool_authorised, load_high_risk_server_names
from agent_runner.tool_governance import ToolGovernancePipeline

logger = logging.getLogger("hermes.runner")


def _strip_ansi(s: str) -> str:
    """Strip ANSI escape codes (terminal colors) from thought text."""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


async def _persist_slot_calls(
    conversation_id: str, message_id: str, agent_id: str, calls: list[dict],
) -> None:
    """Persist one slot's model/tool call records to session_call_logs."""
    if not calls:
        return
    from app.db.models.session_log import SessionCallLog
    async with async_session_maker() as db:
        db.add_all([
            SessionCallLog(
                conversation_id=uuid.UUID(conversation_id),
                message_id=uuid.UUID(message_id),
                agent_id=agent_id,
                **c,
            )
            for c in calls
        ])
        await db.commit()


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
    moa = bool(task.get("moa", False))
    research_mode = bool(task.get("research_mode", False))
    # Resource_link/image blocks for attached files — same structured
    # attachment handling single-agent chat gets. Re-attached to every
    # target's own persona-wrapped text block below.
    attachment_blocks: list[dict] = task.get("content_blocks") or []

    cwd = os.path.join(settings.workspace_root, conversation_id)
    os.makedirs(cwd, exist_ok=True)

    # Tool governance shared by every slot (risk guard active immediately;
    # iteration cap arms once the payload carries max_iterations).
    governance = ToolGovernancePipeline(await load_high_risk_server_names())
    rt_max_iterations = int(task.get("max_iterations") or 0)

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

    async def run_one(slot: int, target: dict) -> tuple[str, str, str, list[dict], list[dict]]:
        """Run one roundtable reply. Returns
        (text, status, thinking, calls, files) where status is complete |
        timeout | error | cancelled. Partial text is preserved on failure. In
        research_mode, slots poll is_cancelled so they early-exit once another
        slot has produced a usable answer."""
        aid = target["agent_id"]
        agent = agents.get(aid) or agents.get("hermes")
        buf = {"text": "", "thinking": "", "tool_calls": 0}
        slot_authorised: set[str] = set()  # high-risk names authorised this slot
        # Late-bound client ref so on_update (defined before `client` exists)
        # can cancel the slot's prompt on a governance denial.
        client_ref: dict = {"client": None}
        reply_status = "complete"
        # Per-slot call collector: every slot shares the roundtable message_id,
        # so calls are attributed per AI via agent_id when persisted.
        call_collector = CallCollector(
            model_name=aid or "hermes",
            turn_started_at=datetime.now(tz=timezone.utc),
        )
        prompt_text = wrap_persona_prompt(text, target.get("system_prompt"))
        # Each target keeps its own persona-wrapped text block but shares the
        # same attachment blocks (a file doesn't change per-participant).
        prompt_content: str | list[dict] = (
            [{"type": "text", "text": prompt_text}, *attachment_blocks]
            if attachment_blocks else prompt_text
        )
        # P2-2 research cascade: if a sibling already answered, skip this slot.
        if research_mode and await R.is_cancelled(conversation_id):
            return buf["text"], "cancelled", "", [], []

        async def on_update(update: dict) -> None:
            kind = update.get("sessionUpdate")
            content = update.get("content") or {}
            if kind == "agent_message_chunk":
                d = content.get("text", "")
                if d:
                    buf["text"] += d
                    await R.publish_event(conversation_id, {
                        "type": "rt_token", "message_id": message_id, "slot": slot, "delta": d
                    })
                    # P2-2 research cascade: first slot to produce text signals
                    # the others to stop. request_cancel sets the conv-level flag
                    # the other slots check before each prompt iteration.
                    if research_mode and not buf.get("signalled"):
                        buf["signalled"] = True
                        try:
                            await R.request_cancel(conversation_id)
                        except Exception:  # noqa: BLE001
                            pass
            elif kind in ("agent_thought_chunk", "agent_thought"):
                # ACP v1 uses agent_thought_chunk; keep agent_thought as a
                # fallback for older hermes-agent versions.
                delta = content.get("text", "") or update.get("delta", "")
                if delta:
                    buf["thinking"] += _strip_ansi(delta)
                    await R.publish_event(conversation_id, {
                        "type": "rt_thought", "message_id": message_id,
                        "slot": slot, "delta": _strip_ansi(delta),
                    })
            elif kind in ("usage", "usage_update"):
                # Model-call accounting for the admin session log (same
                # estimation split single-agent turns use: usage_update has no
                # token detail, so estimate 80/20 from context_used).
                if kind == "usage":
                    tin = content.get("input_tokens", 0) or 0
                    tout = content.get("output_tokens", 0) or 0
                else:
                    used = content.get("used", 0) or 0
                    tin = int(used * 0.8)
                    tout = used - tin
                if tin + tout > 0:
                    call_collector.on_usage(input_tokens=tin, output_tokens=tout)
            elif kind in ("tool_call", "tool_call_begin", "tool_call_end"):
                call_collector.on_tool_call(
                    title=update.get("title"),
                    status=update.get("status") or "completed",
                    tool_kind=content.get("tool_kind") or update.get("tool_kind"),
                    tool_call_id=update.get("toolCallId") or update.get("tool_call_id"),
                )
                # Tool governance — roundtable slots previously ran tool
                # calls with NO risk guard at all (the guard only existed in
                # handle_single). Same pipeline, same authorisation cache.
                # Count once per call: hermes emits either a single tool_call
                # or a begin/end pair — counting end too would double-count and
                # fire the cap at ~half threshold (handle_single counts the same way).
                if kind in ("tool_call", "tool_call_begin"):
                    buf["tool_calls"] = buf.get("tool_calls", 0) + 1
                if not buf.get("gov_blocked"):
                    decision = governance.check(
                        title=update.get("title") or "",
                        tool_calls=buf["tool_calls"],
                        max_iterations=rt_max_iterations,
                        iter_capped=buf.get("iter_capped", False),
                    )
                    if not decision.allowed and decision.reason == "iteration_cap":
                        buf["iter_capped"] = True
                        buf["gov_blocked"] = True
                        TOOL_GOVERNANCE.labels(decision="denied", reason="iteration_cap").inc()
                        c = client_ref["client"]
                        if c is not None:
                            try:
                                await c.cancel()
                            except Exception:  # noqa: BLE001
                                logger.debug("slot cancel failed (cap)", exc_info=True)
                        await R.publish_event(conversation_id, {
                            "type": "iteration_warning", "message_id": message_id,
                            "slot": slot,
                            "tool_calls": decision.extra["tool_calls"],
                            "limit": decision.extra["limit"],
                            "reason": decision.reason,
                        })
                    elif decision.risk_hit and decision.risk_hit not in slot_authorised:
                        if await is_tool_authorised(conversation_id, decision.risk_hit):
                            slot_authorised.add(decision.risk_hit)
                            TOOL_GOVERNANCE.labels(decision="allowed", reason="risk_authorised").inc()
                        else:
                            buf["gov_blocked"] = True
                            TOOL_GOVERNANCE.labels(decision="denied", reason="high_risk_unauthorised").inc()
                            c = client_ref["client"]
                            if c is not None:
                                try:
                                    await c.cancel()
                                except Exception:  # noqa: BLE001
                                    logger.debug("slot cancel failed (risk)", exc_info=True)
                            await R.publish_event(conversation_id, {
                                "type": "tool_blocked", "message_id": message_id,
                                "slot": slot,
                                "tool": decision.risk_hit,
                                "title": decision.title or "",
                                "reason": "high_risk_unauthorised",
                            })

        async def on_fs(path: str, content: str) -> None:
            f = await storage.save_file(uuid.UUID(conversation_id), path, content, aid, uuid.UUID(message_id))
            # Also write to disk so the agent can read its own output later.
            from app.core.files import confine_to_dir, safe_relative_path
            disk_path = confine_to_dir(cwd, safe_relative_path(path))
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)
            with open(disk_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            file_entry = {
                "id": str(f.id), "name": f.name, "kind": f.kind,
                "version": f.current_version,
            }
            buf.setdefault("files", []).append(file_entry)
            await R.publish_event(conversation_id, {
                "type": "file", "message_id": message_id, "slot": slot,
                "file_id": str(f.id),
                "name": f.name, "kind": f.kind, "version": f.current_version,
            })

        client = make_persona_client(
            agent.command, cwd, on_update=on_update, on_fs_write=on_fs,
            profile_dir=target.get("profile_dir"),
        )
        client_ref["client"] = client
        try:
            session_id = await start_persona_session(client, cwd, target.get("mcp_servers"))
            # Nobody can answer an interactive clarify modal mid-roundtable —
            # run_prompt_with_clarify_guard drains and auto-declines any
            # clarify request instead of letting it hang until ACPTimeout.
            # Hard backstop; the prompt preamble asking agents not to call
            # clarify is advisory only and can't be trusted on its own.
            clarify_sid = session_id or conversation_id
            await run_prompt_with_clarify_guard(client, clarify_sid, prompt_content, aid)
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
        # Governance hit (risk guard / iteration cap): the cancel made prompt()
        # return normally, but the slot must NOT be reported complete — its
        # partial text would flow into the merge prompt and persist as a
        # finished answer. Mark it blocked so ok_slots excludes it.
        if buf.get("gov_blocked") and reply_status == "complete":
            reply_status = "blocked"
        await R.publish_event(
            conversation_id,
            {"type": "rt_reply_done", "message_id": message_id, "slot": slot, "status": reply_status},
        )
        return buf["text"], reply_status, buf["thinking"], call_collector.records(), buf.get("files", [])

    results = await asyncio.gather(
        *[run_one(i, t) for i, t in enumerate(targets)], return_exceptions=True
    )
    texts = [r[0] if isinstance(r, tuple) else "（作答失败）" for r in results]
    statuses = [r[1] if isinstance(r, tuple) else "error" for r in results]
    thinkings = [r[2] if isinstance(r, tuple) else "" for r in results]
    call_lists = [r[3] if isinstance(r, tuple) else [] for r in results]
    file_lists = [r[4] if isinstance(r, tuple) else [] for r in results]

    # Persist each slot's model/tool calls (roundtable shares one message_id,
    # so agent_id disambiguates) before any terminal finalize path.
    for i, t in enumerate(targets):
        if call_lists[i]:
            try:
                await _persist_slot_calls(
                    conversation_id, message_id, t["agent_id"], call_lists[i],
                )
            except Exception:  # noqa: BLE001
                logger.debug("Failed to persist slot call log for %s", t["agent_id"], exc_info=True)

    if await R.is_cancelled(conversation_id):
        await _finalize_roundtable(message_id, targets, texts, statuses, thinkings, file_lists, "", "cancelled", moa)
        await R.clear_cancel(conversation_id)
        await R.publish_event(conversation_id, {
            "type": "done", "message_id": message_id, "status": "cancelled"
        })
        return

    ok_slots = [i for i, s in enumerate(statuses) if s == "complete" and texts[i].strip()]

    # P2-2 research mode: first hit wins — return it directly, skip merge.
    # Clear the cancel flag the winning slot set so it doesn't leak.
    if research_mode:
        await R.clear_cancel(conversation_id)
        if ok_slots:
            winner = ok_slots[0]
            merged_text = texts[winner]
            await R.publish_event(conversation_id, {"type": "merge_start", "message_id": message_id})
            await R.publish_event(conversation_id, {
                "type": "merge_token", "message_id": message_id, "delta": merged_text
            })
            await _finalize_roundtable(
                message_id, targets, texts, statuses, thinkings, file_lists, merged_text, "complete", moa,
            )
            await R.publish_event(conversation_id, {
                "type": "done", "message_id": message_id, "status": "complete", "text": merged_text,
            })
            return
        # No slot produced text — fall through to the error path below.
    if not ok_slots:
        await _finalize_roundtable(message_id, targets, texts, statuses, thinkings, file_lists, "", "error", moa)
        await R.clear_cancel(conversation_id)
        await R.publish_event(conversation_id, {
            "type": "error", "message_id": message_id, "detail": "所有助手均作答失败",
        })
        await R.publish_event(conversation_id, {
            "type": "done", "message_id": message_id, "status": "error"
        })
        return

    await R.publish_event(conversation_id, {"type": "merge_start", "message_id": message_id})
    merged = {"text": "", "thinking": ""}
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
        merge_collector = CallCollector(
            model_name=getattr(hermes, "id", "hermes") or "hermes",
            turn_started_at=datetime.now(tz=timezone.utc),
        )

        async def on_merge(update: dict) -> None:
            kind = update.get("sessionUpdate")
            content = update.get("content") or {}
            if kind == "agent_message_chunk":
                d = content.get("text", "")
                if d:
                    merged["text"] += d
                    await R.publish_event(conversation_id, {
                        "type": "merge_token", "message_id": message_id, "delta": d
                    })
            elif kind in ("agent_thought_chunk", "agent_thought"):
                delta = content.get("text", "") or update.get("delta", "")
                if delta:
                    merged["thinking"] += _strip_ansi(delta)
            elif kind in ("usage", "usage_update"):
                if kind == "usage":
                    tin = content.get("input_tokens", 0) or 0
                    tout = content.get("output_tokens", 0) or 0
                else:
                    used = content.get("used", 0) or 0
                    tin = int(used * 0.8)
                    tout = used - tin
                if tin + tout > 0:
                    merge_collector.on_usage(input_tokens=tin, output_tokens=tout)
            elif kind in ("tool_call", "tool_call_begin", "tool_call_end"):
                merge_collector.on_tool_call(
                    title=update.get("title"),
                    status=update.get("status") or "completed",
                    tool_kind=content.get("tool_kind") or update.get("tool_kind"),
                    tool_call_id=update.get("toolCallId") or update.get("tool_call_id"),
                )

        async def _noop(_p: str, _c: str) -> None:
            return None

        mclient = ACPClient(
            hermes.command, cwd, protocol_version=settings.acp_protocol_version,
            on_update=on_merge, on_fs_write=_noop, env=profile_env(None),
            on_permission_request=auto_deny_permission,
        )
        try:
            await mclient.start()
            await mclient.initialize()
            merge_sid = await mclient.new_session(cwd)
            # Merge runs unattended — drain + auto-decline clarify requests the
            # same way slots do, so a clarify call can't block for 300s.
            await run_prompt_with_clarify_guard(
                mclient, merge_sid or conversation_id, merge_prompt, "hermes",
            )
        except ACPTimeout:
            logger.error("roundtable merge timed out")
        except Exception:  # noqa: BLE001
            logger.exception("roundtable merge failed")
        finally:
            await mclient.stop()
        try:
            await _persist_slot_calls(
                conversation_id, message_id, "hermes", merge_collector.records(),
            )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to persist merge call log", exc_info=True)

    await _finalize_roundtable(
        message_id, targets, texts, statuses, thinkings, file_lists, merged["text"], "complete", moa,
        merged_thinking=merged["thinking"],
    )
    await R.clear_cancel(conversation_id)
    await R.publish_event(
        conversation_id, {"type": "done", "message_id": message_id, "status": "complete"}
    )


async def _finalize_roundtable(
    message_id: str, targets: list[dict], texts: list[str],
    statuses: list[str], thinkings: list[str], file_lists: list[list[dict]],
    merged: str, status: str, moa: bool = False,
    merged_thinking: str = "",
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
                        "thinking": thinkings[i] if i < len(thinkings) else "",
                        "files": file_lists[i] if i < len(file_lists) else [],
                    }
                    for i in range(len(targets))
                ],
                "merged": {
                    "text": merged,
                    "thinking": merged_thinking,
                    "status": status,
                },
                "moa": moa,
            }
            msg.status = status
            convo = await db.get(Conversation, msg.conversation_id)
            if convo:
                convo.updated_at = datetime.now(tz=timezone.utc)
            await db.commit()
