"""Memory consolidation (做梦整理记忆): summarize history into AgentMemory."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncio

from sqlalchemy import select

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.conversation import Conversation, Message
from app.db.models.user import User
from agent_runner.acp_client import ACPClient, ACPTimeout
from agent_runner.acp_client import auto_deny_permission

logger = logging.getLogger("hermes.runner")

_MEMORY_KEYS = ("user_profile", "soul", "notes")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

CONSOLIDATE_PROMPT = """【记忆整理任务】你正在执行"做梦"式记忆整理。请基于下方的现有长期记忆和近期对话摘录，更新这位用户的三段长期记忆。

要求：
1. 合并现有记忆与对话中的新信息：保留仍然有效的旧内容，补充新洞察，删除过时或重复的内容。
2. user_profile（用户画像）：用户的职业背景、技术栈、关注领域、沟通偏好等客观事实。
3. soul（个性设定）：AI 应当以什么角色、语气、风格与该用户互动。
4. notes（我的笔记）：值得长期记住的具体事项（进行中的项目、重要约定、待办背景等）。
5. 三段内容总字数不得超过 {budget} 字。宁可精炼，不要堆砌。
6. 对话摘录中 AI 的某些回复可能包含对工具调用结果（如文件内容、搜索结果）的复述，这些属于执行任务的中间输出，不是用户主动提供的信息。整理时请忽略这类复述，只提取用户与 AI 之间的真实交流内容。
7. 另外，【近期对话摘录】按 "## 会话「标题」" 分为若干节，请为每一节单独写一段不超过 200 字的摘要（这段话之后会被单独存起来按需检索，所以要能独立说明这节对话讲了什么），按节出现的顺序放入 "episodes" 数组，数组长度必须与分节数完全一致，一节都不能少。
8. 只输出一个 JSON 对象，不要输出任何其他文字、解释或 markdown 代码块：
{{"user_profile": "...", "soul": "...", "notes": "...", "episodes": ["...", "..."]}}

【现有记忆】
[用户画像]
{user_profile}

[个性设定]
{soul}

[我的笔记]
{notes}

【近期对话摘录】
{excerpts}
"""


def _extract_json_dict(text: str) -> dict | None:
    """Find the first JSON object in LLM output, tolerating markdown fences
    and surrounding prose. Returns None if nothing parses as a dict."""
    candidates: list[str] = []
    m = _JSON_FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for raw in candidates:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_memory_json(text: str) -> dict[str, str] | None:
    """Extract {"user_profile","soul","notes"} from LLM output.

    Tolerates markdown fences and surrounding prose. Returns None on failure.
    """
    data = _extract_json_dict(text)
    if data is None:
        return None
    out: dict[str, str] = {}
    for key in _MEMORY_KEYS:
        val = data.get(key)
        out[key] = val.strip() if isinstance(val, str) else ""
    if any(out.values()):
        return out
    return None


def parse_episode_summaries(text: str, expected_count: int) -> list[str] | None:
    """Extract the per-conversation "episodes" array from the same
    consolidation response, positionally aligned with the input sections.

    Returns None (skip episode creation for this run, don't fail the whole
    consolidation) if the field is missing, malformed, or its length doesn't
    match expected_count — a prompt/parsing regression here must not
    regress the already-shipped flat user_profile/soul/notes update.
    """
    data = _extract_json_dict(text)
    if data is None:
        return None
    episodes = data.get("episodes")
    if not isinstance(episodes, list) or len(episodes) != expected_count:
        return None
    if not all(isinstance(e, str) and e.strip() for e in episodes):
        return None
    return [e.strip() for e in episodes]


def trim_memory_to_budget(mem: dict[str, str], budget: int) -> dict[str, str]:
    """Proportionally trim the three fields so their total length <= budget."""
    total = sum(len(v) for v in mem.values())
    if total <= budget:
        return mem
    ratio = budget / total
    out: dict[str, str] = {}
    used = 0
    for i, k in enumerate(_MEMORY_KEYS):
        if i == len(_MEMORY_KEYS) - 1:
            allowed = budget - used  # rounding slack goes to the last field
        else:
            allowed = int(len(mem[k]) * ratio)
        out[k] = mem[k][:max(0, allowed)]
        used += len(out[k])
    return out


def _message_excerpt(msg: Message) -> str | None:
    """One transcript line for the consolidation prompt; None to skip.

    Only genuine user<->agent dialogue is kept. Roundtable merges, system
    prompts, error turns and bare tool-call stubs are discarded.
    """
    content = msg.content or {}
    if msg.role == "system" or msg.status == "error":
        return None
    # Roundtable messages are synthesized by the orchestrator, not raw
    # user-to-agent dialogue, and often embed tool outputs / hidden prompts.
    if msg.role == "roundtable":
        return None
    if msg.role == "agent":
        text = (content.get("text") or "").strip()
        # Skip turns that are purely tool calls with no conversational text.
        if not text:
            return None
        if content.get("tool_calls") and len(text) < 20:
            return None
        prefix = "AI"
    elif msg.role == "user":
        text = (content.get("text") or "").strip()
        prefix = "用户"
    else:
        return None
    if not text:
        return None
    limit = settings.memory_consolidate_msg_chars
    if len(text) > limit:
        text = text[:limit] + "…"
    return f"{prefix}: {text}"


@dataclass
class _ConsolidationPlan:
    """One LLM consolidation run: the conversations of a single profile
    (profile_id=None → the global memory layer), its current memory as the
    "existing memory" baseline, and the excerpt sections to feed the prompt."""

    profile_id: uuid.UUID | None
    sections: list[str] = field(default_factory=list)
    # Parallel to sections — (conversation_id, title, raw_chars) for writing
    # one MemoryEpisode per section once the LLM returns summaries in order.
    episode_meta: list[tuple[uuid.UUID, str, int]] = field(default_factory=list)
    old: dict[str, str] = field(default_factory=dict)


def _build_plan_sections(
    convos: list[Conversation], msgs_by_conv: dict[uuid.UUID, list[Message]], budget: int,
) -> tuple[list[str], list[tuple[uuid.UUID, str, int]]]:
    """Build excerpt sections for a group of conversations within the budget."""
    sections: list[str] = []
    episode_meta: list[tuple[uuid.UUID, str, int]] = []
    used = 0
    for convo in convos:
        lines = [e for m in msgs_by_conv.get(convo.id, []) if (e := _message_excerpt(m))]
        if not lines:
            continue
        section = f"## 会话「{convo.title}」\n" + "\n".join(lines)
        if used + len(section) > budget:
            section = section[: budget - used]
        sections.append(section)
        episode_meta.append((convo.id, convo.title, len(section)))
        used += len(section)
        if used >= budget:
            break
    return sections, episode_meta


async def handle_memory_consolidate(task: dict, agents: dict) -> None:
    """Handle memory consolidation task."""
    from app.services import memory_service

    user_id = task["user_id"]
    r = R.get_redis()
    status_key = R.mem_consolidate_status_key(user_id)

    async def _set_status(status: str, detail: str | None = None) -> None:
        payload: dict = {
            "status": status,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        if detail:
            payload["detail"] = detail
        ttl = (
            settings.memory_consolidate_lock_ttl
            if status == "running"
            else settings.memory_consolidate_status_ttl
        )
        await r.set(status_key, json.dumps(payload, ensure_ascii=False), ex=ttl)

    consolidation_ts = datetime.now(tz=timezone.utc)

    try:
        async with async_session_maker() as db:
            stmt = select(Conversation).where(Conversation.owner_id == uuid.UUID(user_id))
            stmt = stmt.order_by(Conversation.updated_at.desc()).limit(
                settings.memory_consolidate_max_conversations
            )
            convos = list((await db.execute(stmt)).scalars().all())

            # Batch-load messages for all conversations in a single query
            # instead of N per-conversation round-trips (N+1 prevention).
            convos_by_id = {c.id: c for c in convos}
            if convos_by_id:
                all_msgs = (
                    await db.execute(
                        select(Message)
                        .where(Message.conversation_id.in_(list(convos_by_id.keys())))
                        .order_by(Message.conversation_id, Message.created_at.asc())
                    )
                ).scalars().all()
            else:
                all_msgs = []

            # Group messages by conversation_id (preserving the convos order).
            msgs_by_conv: dict[uuid.UUID, list[Message]] = {}
            for m in all_msgs:
                msgs_by_conv.setdefault(m.conversation_id, []).append(m)

            # Per-profile memory semantics: each profile's conversations are
            # consolidated against THAT profile's memory row (global rows fall
            # back to the shared row as baseline); profile-less conversations
            # land in the global layer. One plan per profile with content.
            groups: dict[uuid.UUID | None, list[Conversation]] = {}
            for c in convos:
                groups.setdefault(c.profile_id, []).append(c)

            budget = settings.memory_consolidate_input_chars
            plans: list[_ConsolidationPlan] = []
            for pid, group_convos in groups.items():
                mem = await memory_service.get_memory(db, uuid.UUID(user_id), profile_id=pid)
                since = mem.last_consolidated_at if mem else None
                recent = [c for c in group_convos if since is None or c.updated_at > since]
                if not recent:
                    continue
                sections, episode_meta = _build_plan_sections(recent, msgs_by_conv, budget)
                if not sections:
                    continue
                old = {
                    "user_profile": (mem.user_profile if mem else "") or "",
                    "soul": (mem.soul if mem else "") or "",
                    "notes": (mem.notes if mem else "") or "",
                }
                if mem is None or not mem.last_consolidated_at:
                    user = await db.get(User, uuid.UUID(user_id))
                    if user and user.preferences:
                        prefs = user.preferences
                        pref_lines = [f"- {k}: {v}" for k, v in prefs.items() if v]
                        if pref_lines:
                            legacy = "【旧版偏好设置（自动迁移）】\n" + "\n".join(pref_lines)
                            old["notes"] = (old["notes"] + "\n\n" + legacy).strip() if old["notes"] else legacy
                plans.append(_ConsolidationPlan(profile_id=pid, sections=sections,
                                                episode_meta=episode_meta, old=old))

        if not plans:
            await _set_status("done", "没有新的对话内容，记忆保持不变")
            return

        agent = agents.get("hermes") or next(iter(agents.values()), None)
        if agent is None:
            await _set_status("error", "没有可用的 agent")
            return

        cwd = os.path.join(settings.workspace_root, f"memconsol-{user_id}")
        await asyncio.to_thread(os.makedirs, cwd, exist_ok=True)
        # Isolated HERMES_HOME for the subprocess: without it the agent's own
        # memory tools write the GLOBAL ~/.hermes memories/MEMORY.md and
        # memory_store.db while the platform writes Postgres — an implicit
        # double-write that drifts. Pointing it at a scratch home keeps the
        # consolidation read (the prompt already carries the existing memory)
        # and any incidental memory writes inside this run's scratch dir.
        tmp_home = os.path.join(cwd, "hermes-home")
        await asyncio.to_thread(os.makedirs, tmp_home, exist_ok=True)

        async def on_update(update: dict) -> None:
            if update.get("sessionUpdate") == "agent_message_chunk":
                buf["text"] += (update.get("content") or {}).get("text", "")

        async def _noop_fs(_p: str, _c: str) -> None:
            return None

        # One LLM run per plan; a failing group must not sink the others.
        results: list[tuple[_ConsolidationPlan, dict[str, str], list[str] | None]] = []
        for plan in plans:
            prompt = CONSOLIDATE_PROMPT.format(
                budget=settings.memory_total_chars,
                excerpts="\n\n".join(plan.sections),
                user_profile=plan.old["user_profile"] or "（空）",
                soul=plan.old["soul"] or "（空）",
                notes=plan.old["notes"] or "（空）",
            )
            buf = {"text": ""}
            client = ACPClient(
                agent.command, cwd, protocol_version=settings.acp_protocol_version,
                on_update=on_update, on_fs_write=_noop_fs,
                on_permission_request=auto_deny_permission,
                env={"HERMES_HOME": tmp_home},
            )
            try:
                await client.start()
                await client.initialize()
                await client.new_session(cwd)
                await client.prompt(prompt)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "memory_consolidate: group profile_id=%s failed for %s: %s",
                    plan.profile_id, user_id[:8], type(exc).__name__,
                )
                continue
            finally:
                await client.stop()

            parsed = parse_memory_json(buf["text"])
            if parsed is None:
                logger.warning(
                    "memory_consolidate: unparseable output for %s (profile_id=%s): %r",
                    user_id[:8], plan.profile_id, buf["text"][:300],
                )
                continue
            for k in _MEMORY_KEYS:
                parsed[k] = parsed[k] or plan.old[k]
            parsed = trim_memory_to_budget(parsed, settings.memory_total_chars)
            # Per-conversation summaries for the searchable episodic layer —
            # best effort: a missing/malformed "episodes" field skips episode
            # creation for this group without failing the flat update.
            episode_summaries = parse_episode_summaries(buf["text"], len(plan.episode_meta))
            if episode_summaries is None and plan.episode_meta:
                logger.info(
                    "memory_consolidate: no usable per-section episodes for %s (profile_id=%s, expected %d)",
                    user_id[:8], plan.profile_id, len(plan.episode_meta),
                )
            results.append((plan, parsed, episode_summaries))

        if not results:
            await _set_status("error", "AI 输出无法解析，记忆未变更")
            return

        conv_profile: dict[uuid.UUID, uuid.UUID | None] = {
            c.id: c.profile_id for c in convos
        }
        async with async_session_maker() as db:
            for plan, parsed, episode_summaries in results:
                # Flat memory lands on the plan's own profile row (global row
                # for profile-less conversations) — never the shared fallback.
                await memory_service.upsert_memory(
                    db, uuid.UUID(user_id), profile_id=plan.profile_id,
                    notes=parsed["notes"], user_profile=parsed["user_profile"],
                    soul=parsed["soul"], last_consolidated_at=consolidation_ts,
                )
                if episode_summaries:
                    # Episodes are attributed to the conversation's profile when
                    # it has one, so each assistant's episodic recall stays
                    # scoped; profile-less conversations land in the global layer.
                    for (conv_id, title, raw_chars), summary in zip(plan.episode_meta, episode_summaries):
                        await memory_service.add_episode(
                            db, uuid.UUID(user_id), conv_id, title, summary, raw_chars,
                            consolidation_ts, profile_id=conv_profile.get(conv_id),
                            commit=False,
                        )
            # Single commit for all groups instead of N round-trips.
            await db.commit()
        await _set_status("done", f"整理完成（{len(results)} 组）")
    except ACPTimeout:
        await _set_status("error", "整理超时")
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory_consolidate failed for %s", user_id[:8])
        await _set_status("error", f"整理失败: {type(exc).__name__}")
