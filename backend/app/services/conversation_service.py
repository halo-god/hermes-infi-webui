"""Conversation + message persistence and the send→enqueue hot path."""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json as _json

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import redis as redis_core
from app.core.files import confine_to_dir, safe_relative_path, OFFICE_EXTRACTORS
from app.db.models.agent import Profile
from app.db.models.conversation import Conversation, GroupMember, Message
from app.db.models.workspace import WorkspaceFile, WorkspaceFileVersion


async def list_conversations(
    db: AsyncSession,
    owner_id: uuid.UUID,
    *,
    q: str | None = None,
    pinned_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    use_cache: bool = True,
) -> list[Conversation]:
    # Build cache key for list queries (without search)
    # NOTE: We don't cache list results because they contain full ORM objects
    # which can't be properly serialized/deserialized. Caching is only used
    # for invalidation tracking.

    # Personal conversations (owned) + group conversations (member of)
    group_subq = (
        select(GroupMember.conversation_id)
        .where(GroupMember.user_id == owner_id)
        .scalar_subquery()
    )
    stmt = select(Conversation).where(
        ((Conversation.owner_id == owner_id)
        | ((Conversation.type == "group") & Conversation.id.in_(group_subq)))
        & (Conversation.title != "__file_storage__")
        # Background subagent conversations are headless — surfaced only via
        # the parent conversation's subagent panel, never the sidebar.
        & (Conversation.type != "subagent")
    )
    if pinned_only:
        stmt = stmt.where(Conversation.pinned.is_(True))
    if q:
        # Case-insensitive substring match on title. Postgres full-text search
        # (to_tsvector/plainto_tsquery with the 'simple' config) can't do
        # substring matching and has no CJK word segmentation, so a query like
        # "关键词" never matches a title "唯一关键词". ILIKE '%q%' gives correct
        # substring semantics across languages; title is short so it's cheap.
        like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Conversation.title.ilike(f"%{like}%"))
    stmt = stmt.order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
    # Bound the query — a user with thousands of conversations must not pull
    # them all in one request. Callers page with limit/offset.
    stmt = stmt.offset(max(0, offset)).limit(max(1, min(limit, 200)))
    result = list((await db.execute(stmt)).scalars().all())

    return result


async def bulk_delete(
    db: AsyncSession, owner_id: uuid.UUID, ids: list[uuid.UUID]
) -> int:
    result = await db.execute(
        delete(Conversation).where(
            Conversation.owner_id == owner_id, Conversation.id.in_(ids)
        )
    )
    await db.commit()
    return result.rowcount or 0


async def get_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, owner_id: uuid.UUID
) -> Conversation | None:
    res = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            (Conversation.owner_id == owner_id)
            | Conversation.is_channel.is_(True)
            | (Conversation.type == "group"),  # group members can access
        )
    )
    return res.scalar_one_or_none()


async def get_messages(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    limit: int | None = None,
    before_id: uuid.UUID | None = None,
) -> list[Message]:
    """Fetch messages, optionally paginated.

    When *limit* is given, returns only the most recent *limit* messages.
    *before_id* (cursor) fetches messages older than the given message id.
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
    )
    if before_id:
        # Sub-select to get the cursor timestamp
        cursor_ts = (
            select(Message.created_at)
            .where(Message.id == before_id)
            .scalar_subquery()
        )
        stmt = stmt.where(Message.created_at < cursor_ts)
    stmt = stmt.order_by(Message.created_at.desc(), Message.role.asc())
    if limit:
        stmt = stmt.limit(limit)
    res = await db.execute(stmt)
    return list(reversed(res.scalars().all()))


async def create_conversation(
    db: AsyncSession,
    owner_id: uuid.UUID,
    *,
    title: str | None,
    primary_agent_id: str,
    profile_id: str | None,
    team_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> Conversation:
    convo = Conversation(
        owner_id=owner_id,
        title=title or "新会话",
        primary_agent_id=primary_agent_id,
        profile_id=profile_id,
        team_id=team_id,
        project_id=project_id,
    )
    db.add(convo)
    await db.commit()
    await db.refresh(convo)
    # Invalidate conversation list cache
    return convo


async def _resolve_attached_files(
    db: AsyncSession, file_ids: list[str], conversation_id: str | None = None,
) -> list[dict]:
    """Look up workspace files by id, write to agent workspace dir, return metadata + content.

    Returns [{id, name, kind, workspace_path, content, size_bytes, mime_type, folder_path}].
    """
    if not file_ids:
        return []

    # MIME type mapping
    MIME_MAP = {
        "md": "text/markdown", "txt": "text/plain", "json": "application/json",
        "csv": "text/csv", "html": "text/html", "htm": "text/html",
        "js": "text/javascript", "ts": "text/javascript", "py": "text/x-python",
        "go": "text/x-go", "rs": "text/x-rust", "yaml": "text/yaml",
        "yml": "text/yaml", "toml": "text/toml", "sh": "text/x-shellscript",
        "bash": "text/x-shellscript", "log": "text/plain", "xml": "text/xml",
        "css": "text/css", "diff": "text/x-diff", "patch": "text/x-diff",
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
        "bmp": "image/bmp", "pdf": "application/pdf",
    }
    IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"}
    TEXT_EXTS = {"md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
                 "yaml", "yml", "toml", "sh", "bash", "log", "xml", "css", "diff", "patch", "pdf",
                 "docx", "xlsx", "pptx"}

    # Prepare workspace dir for the agent to access files
    ws_dir = None
    if conversation_id:
        ws_dir = os.path.join(settings.workspace_root, conversation_id, "attachments")
        os.makedirs(ws_dir, exist_ok=True)

    result = []
    for raw_id in file_ids:
        try:
            fid = uuid.UUID(str(raw_id))
        except ValueError:
            continue
        f = await db.get(WorkspaceFile, fid)
        if f is not None:
            ext = (f.kind or "").lower()
            mime = MIME_MAP.get(ext, "application/octet-stream")
            is_image = ext in IMAGE_EXTS
            is_text = ext in TEXT_EXTS
            folder = (f.folder_path or "").strip("/")

            # Resolve content: storage_key wins, then inline content
            file_content = f.content or ""
            raw_bytes: bytes | None = None
            if f.storage_key and not file_content:
                try:
                    import asyncio
                    from app.core import object_storage
                    raw_bytes = await asyncio.to_thread(object_storage.get, f.storage_key)
                    if ext in OFFICE_EXTRACTORS:
                        # Extract full HTML preview for office docs — much smaller than base64
                        file_content = OFFICE_EXTRACTORS[ext](raw_bytes) or ""
                    elif is_text and ext != "pdf":
                        file_content = raw_bytes.decode("utf-8", "ignore")
                    else:
                        import base64
                        file_content = base64.b64encode(raw_bytes).decode("ascii")
                except Exception:
                    file_content = ""

            # Build relative path preserving folder structure (e.g. "testfolder/foo.md")
            rel_name = safe_relative_path(f.name)
            rel_path = f"{folder}/{rel_name}" if folder else rel_name

            # Write file content to workspace so agent can read it — confine
            # the (possibly agent-authored) name so it can't escape ws_dir.
            if ws_dir and file_content and not is_image:
                fpath = confine_to_dir(ws_dir, rel_path)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                if raw_bytes and ext == "pdf":
                    with open(fpath, "wb") as fh:
                        fh.write(raw_bytes)
                else:
                    with open(fpath, "w", encoding="utf-8") as fh:
                        fh.write(file_content)

            result.append({
                "id": str(f.id), "name": f.name, "kind": f.kind,
                "folder_path": folder,
                "workspace_path": f"attachments/{rel_path}" if ws_dir and file_content else None,
                "content": file_content,
                "size_bytes": f.size_bytes or len(file_content),
                "mime_type": mime,
                "is_image": is_image,
                "is_text": is_text,
            })
    return result


# ── Prompt directives (single source — these used to be duplicated inline) ──

_FILE_WRITE_PREAMBLE = (
    "【文件写入规范】当你需要为用户创建、生成或导出文件时，"
    "必须使用 write_file 工具将文件写入当前工作目录（cwd）。"
    "文件路径使用相对路径（如 'README.md'、'src/main.py'），不要使用绝对路径。"
    "不要只在回复文本中说\"文件已生成\"或给出文件路径而不实际写入。"
    "文件名请使用有意义的名称（如 会议纪要.md、report.csv），不要使用临时路径。"
)

_CLARIFY_PREAMBLE = (
    "\n\n【强制规则：必须先确认再行动】\n"
    "当用户的请求有以下任一情况时，你必须先调用 clarify 工具，不要直接回答：\n"
    "- 请求模糊、有多种理解方式\n"
    "- 需要用户选择方向、风格、范围\n"
    "- 涉及重要决策或有风险的操作\n\n"
    "调用方式（必须是工具调用，不要输出文本格式）：\n"
    'clarify(question="问题", choices=["选项A", "选项B", "选项C"])\n'
    'clarify(question="你具体想要什么？")  # 无选项时用 open-ended\n\n'
    "禁止在回复文本中输出 [确认] 或类似的标记格式。必须通过工具调用 clarify。\n"
    "违反此规则会导致用户不满。记住：先问再做。"
)

_ANTI_CLARIFY = (
    "重要：用户在对话中的简短回复（如'继续'、'好的'、'是的'、'ok'、单句指令等）"
    "是明确的意图表达，不要调用 clarify 工具追问。直接执行用户的意图即可。"
    "只有当用户的请求真正存在多种互不相同的理解方式时才需要澄清。"
)

# Roundtable replies run without the clarify polling loop — a clarify call
# there would block the agent until timeout with nobody able to answer.
_NO_CLARIFY_ROUNDTABLE = (
    "\n\n注意：当前是多助手圆桌模式，无法弹出交互确认，不要调用 clarify 工具。"
    "如有歧义请基于最合理的假设直接作答，并简要说明你的假设。"
)


def _clarify_directives(is_first_turn: bool, text: str) -> str:
    """Clarify preamble for the FIRST turn of a conversation only.

    Follow-up turns get the anti-clarify line via the system prompt instead;
    injecting both used to hand the model contradictory instructions on every
    short reply ("必须 clarify" vs "不要 clarify").
    """
    if (settings.clarify_strategy or "").strip().lower() == "disabled":
        return ""
    if not is_first_turn:
        return ""
    return _CLARIFY_PREAMBLE


async def send_user_only(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> tuple[Message, None]:
    """Save a user message without triggering agent (for channel mention mode)."""
    user_msg = Message(
        conversation_id=convo.id,
        owner_id=owner_id,
        role="user",
        content={"text": text},
        status="complete",
        task_id=task_id,
    )
    db.add(user_msg)
    if convo.title == "新会话":
        convo.title = text[:40]
    await db.commit()
    await db.refresh(user_msg)
    return user_msg, None


def _profile_dir(profile: Profile | None) -> str | None:
    """Directory containing the profile's config.yaml — becomes HERMES_HOME for
    the spawned agent so config/memory/sessions scope to the selected profile."""
    if profile is None or not profile.path:
        return None
    return os.path.dirname(os.path.expanduser(profile.path))


def _mcp_server_entry(server: dict) -> dict | None:
    """Convert one admin-registered MCP server record into the ACP `mcpServers`
    session-param shape.

    NOTE: the exact field names a real `hermes` ACP agent expects for an
    `mcpServers` entry aren't observable anywhere in this repo (the param has
    never been populated until now) — this mapping follows the conventional
    stdio/SSE MCP client-config shape and may need adjustment once verified
    against the real agent binary. Isolated here so that's a one-place fix.
    """
    name = server.get("name")
    if not name:
        return None
    transport = server.get("transport", "stdio")
    if transport == "stdio":
        command = (server.get("command") or "").strip()
        if not command:
            return None
        parts = command.split()
        return {"name": name, "command": parts[0], "args": parts[1:], "env": server.get("env") or {}}
    url = server.get("url")
    if not url:
        return None
    return {"name": name, "type": "sse", "url": url, "headers": server.get("env") or {}}


async def _resolve_mcp_servers(db: AsyncSession, profile: Profile | None) -> list[dict]:
    """Resolve a Profile's enabled MCP server names against the admin-managed
    catalog, converted to the ACP session-param shape.

    No profile or no servers enabled -> [] (default off, not default-all —
    a chat session shouldn't silently gain tool access just because the admin
    registered a server somewhere)."""
    names = set(getattr(profile, "mcp_server_names", None) or []) if profile else set()
    if not names:
        return []
    from app.services import settings_service

    settings_row = await settings_service.get(db)
    catalog: list[dict] = (settings_row.data or {}).get("mcp_servers", [])
    entries = [_mcp_server_entry(s) for s in catalog if s.get("name") in names]
    return [e for e in entries if e]


async def _build_moa_targets(
    db: AsyncSession, target_profile_ids: list[str], memory_prompt: str | None,
) -> list[dict]:
    """Resolve an MoA Profile's reference profiles into roundtable `targets`,
    each with its own persona/env/tools — same shape send_roundtable already
    expects from multi-agent compare and group roundtable dispatch."""
    targets: list[dict] = []
    for target_pid in target_profile_ids:
        target_profile = await db.get(Profile, target_pid)
        if not target_profile or not target_profile.is_active:
            continue
        t_system_prompt = target_profile.system_prompt or None
        t_knowledge_prompt = await _build_knowledge_prompt(db, target_profile)
        if t_knowledge_prompt:
            t_system_prompt = (
                f"{t_system_prompt}\n\n{t_knowledge_prompt}" if t_system_prompt else t_knowledge_prompt
            )
        if memory_prompt:
            t_system_prompt = f"{t_system_prompt}\n\n{memory_prompt}" if t_system_prompt else memory_prompt
        targets.append({
            "agent_id": target_profile.default_agent_id,
            "profile_id": str(target_profile.id),
            "system_prompt": t_system_prompt,
            "profile_dir": _profile_dir(target_profile),
            "mcp_servers": await _resolve_mcp_servers(db, target_profile),
        })
    return targets


def _build_attached_prompt(text: str, attached: list[dict]) -> str:
    """Inject file references into the prompt text so the agent knows about attachments.

    Files are already written to the workspace directory; the agent needs to know
    their relative paths to read them via read_file / read_image tools. References
    are wrapped in lightweight markers so the agent can distinguish them from user text.
    """
    if not attached:
        return text
    refs: list[str] = []
    for f in attached:
        ws_path = f.get("workspace_path")
        size = f.get("size_bytes", 0)
        if ws_path:
            # For large text/office files, suggest chunked reading
            if size > 30000 and (f.get("is_text") or f.get("kind") in ("docx", "xlsx", "pptx")):
                refs.append(
                    f"- {f['name']} ({ws_path}) — 文件较大，建议使用 read_file 分段读取"
                )
            else:
                refs.append(f"- {f['name']} ({ws_path})")
        elif f.get("is_image"):
            refs.append(f"- {f['name']} (image attached)")
        else:
            refs.append(f"- {f['name']}")
    if refs:
        return f"{text}\n\n【当前对话已引用以下文件】\n" + "\n".join(refs)
    return text


async def send_message(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    system_prompt: str | None = None,
    existing_user_msg: Message | None = None,
    profile_dir: str | None = None,
    mcp_servers: list[dict] | None = None,
    agent_id_override: str | None = None,
    profile_id: str | None = None,
    task_id: uuid.UUID | None = None,
    matched_skill_ids: list[uuid.UUID] | None = None,
) -> tuple[Message, Message]:
    """Persist the user turn + an empty streaming agent turn, then enqueue ACP work.

    The per-token hot path does NOT touch the DB — the runner streams events
    and writes the agent message once on completion. Pass existing_user_msg
    when the caller already persisted the user turn (group dispatch) to avoid
    a duplicate user row. Pass agent_id_override to attribute the reply to a
    specific @-mentioned agent without mutating the conversation's default.
    Pass profile_id to record exactly which Profile answered (disambiguates
    when multiple Profiles share one agent_id).
    """
    # NOTE: read acp_session_id before any commit expires the instance —
    # _clarify_directives needs it to detect first-turn vs follow-up.
    is_first_turn = convo.acp_session_id is None
    reply_agent_id = agent_id_override or convo.primary_agent_id

    attached = await _resolve_attached_files(db, attached_file_ids or [], conversation_id=str(convo.id))
    if existing_user_msg is None:
        user_content: dict = {"text": text}
        if attached:
            # Persist only lightweight metadata (no file content) to avoid
            # PostgreSQL JSONB \u0000 rejection and message bloat.
            user_content["files"] = [
                {"id": f["id"], "name": f["name"], "kind": f.get("kind")}
                for f in attached
            ]
        user_msg = Message(
            conversation_id=convo.id,
            owner_id=owner_id,
            role="user",
            content=user_content,
            status="complete",
            task_id=task_id,
        )
        db.add(user_msg)
    else:
        user_msg = existing_user_msg
    parsed_profile_id: uuid.UUID | None = None
    if profile_id:
        try:
            parsed_profile_id = uuid.UUID(profile_id)
        except (ValueError, TypeError):
            parsed_profile_id = None
    agent_msg = Message(
        conversation_id=convo.id,
        role="agent",
        agent_id=reply_agent_id,
        profile_id=parsed_profile_id,
        content={"text": ""},
        status="streaming",
    )
    db.add(agent_msg)

    # Auto-title from the first user message.
    if convo.title == "新会话":
        convo.title = text[:40]

    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(agent_msg)

    # Build prompt — use ACP content blocks for structured attachment handling.
    # Images: ImageContentBlock (base64), Text files: Resource Link + inline fallback.
    # Build content blocks for ACP protocol
    prompt_blocks: list[dict] = []

    # Text block: preambles + user message + inline file references
    prompt_text = _build_attached_prompt(text, attached)
    full_text = f"{_FILE_WRITE_PREAMBLE}{_clarify_directives(is_first_turn, text)}\n\n{prompt_text}"
    prompt_blocks.append({"type": "text", "text": full_text})

    # Add Resource Link blocks for attached files (agent can read from workspace)
    for f in attached:
        ws_path = f.get("workspace_path")
        if ws_path and not f.get("is_image"):
            cwd = os.path.join(settings.workspace_root, str(convo.id))
            abs_path = os.path.join(cwd, ws_path)
            prompt_blocks.append({
                "type": "resource_link",
                "uri": f"file://{abs_path}",
                "name": f["name"],
                "mimeType": f.get("mime_type", "application/octet-stream"),
                "size": f.get("size_bytes", 0),
            })

    # Add ImageContentBlock for image attachments
    for f in attached:
        if f.get("is_image") and f.get("content"):
            prompt_blocks.append({
                "type": "image",
                "mimeType": f.get("mime_type", "image/png"),
                "data": f["content"],  # already base64 from upload
            })

    await redis_core.clear_cancel(str(convo.id))
    await redis_core.enqueue_prompt(
        {
            "type": "single",
            "conversation_id": str(convo.id),
            "message_id": str(agent_msg.id),
            "agent_id": reply_agent_id,
            "text": full_text,
            "content_blocks": prompt_blocks if len(prompt_blocks) > 1 else None,
            "system_prompt": system_prompt,
            "profile_dir": profile_dir,
            "mcp_servers": mcp_servers or [],
            "matched_skill_ids": [str(sid) for sid in (matched_skill_ids or [])],
            "skill_firing_excerpt": text[:500],
        }
    )
    return user_msg, agent_msg


async def send_roundtable(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    targets: list[dict],
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    mentions: list[str] | None = None,
    existing_user_msg: Message | None = None,
    task_id: uuid.UUID | None = None,
    moa: bool = False,
) -> tuple[Message, Message]:
    """Multi-agent turn: one roundtable message holding per-agent replies + a
    synthesized merge. The runner streams each reply in parallel — each with
    its own resolved persona — then merges.

    `targets` is a list of {"agent_id", "profile_id", "system_prompt",
    "profile_dir"} dicts, one per distinct AI participant. Profiles sharing
    an agent_id are NOT deduped here — each keeps its own persona/env so the
    roundtable actually differs per participant instead of collapsing to one
    shared identity.

    Pass existing_user_msg when the caller already persisted the user turn
    (group dispatch) to avoid a duplicate user row. Pass moa=True when this
    fan-out was triggered by selecting an is_moa Profile rather than an
    explicit multi-agent/group roundtable, so the frontend can render it as a
    single synthesized answer instead of a side-by-side compare.
    """
    attached = await _resolve_attached_files(db, attached_file_ids or [], conversation_id=str(convo.id))
    if existing_user_msg is None:
        user_content: dict = {"text": text}
        if attached:
            user_content["files"] = [
                {"id": f["id"], "name": f["name"], "kind": f.get("kind")}
                for f in attached
            ]
        user_msg = Message(
            conversation_id=convo.id, owner_id=owner_id, role="user", content=user_content, mentions=mentions or [], status="complete", task_id=task_id
        )
    else:
        user_msg = existing_user_msg
    rt_msg = Message(
        conversation_id=convo.id,
        role="roundtable",
        agent_id=targets[0]["agent_id"],
        content={
            "replies": [
                {
                    "agent_id": t["agent_id"],
                    "profile_id": t.get("profile_id"),
                    "text": "",
                    "status": "streaming",
                }
                for t in targets
            ],
            "merged": {"text": "", "status": "pending"},
            "moa": moa,
        },
        status="streaming",
    )
    if existing_user_msg is None:
        db.add_all([user_msg, rt_msg])
    else:
        db.add(rt_msg)
    if convo.title == "新会话":
        convo.title = text[:40]
    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(rt_msg)

    prompt_text = _build_attached_prompt(text, attached)

    # File-write instructions for roundtable agents; clarify is explicitly
    # disallowed here because nobody can answer a modal mid-roundtable. The
    # runner also hard-rejects any clarify call as a backstop (see
    # runner_roundtable.py) — this preamble alone is advisory, not enforced.
    prompt_text = f"{_FILE_WRITE_PREAMBLE}{_NO_CLARIFY_ROUNDTABLE}\n\n{prompt_text}"

    await redis_core.clear_cancel(str(convo.id))
    await redis_core.enqueue_prompt(
        {
            "type": "roundtable",
            "conversation_id": str(convo.id),
            "message_id": str(rt_msg.id),
            "targets": targets,
            "text": prompt_text,
            "moa": moa,
        }
    )
    return user_msg, rt_msg


async def _build_memory_prompt(db: AsyncSession, owner_id: uuid.UUID | None) -> str | None:
    """Load the user's agent memory and format it as a system-prompt section."""
    if not owner_id:
        return None
    from app.services import memory_service

    mem = await memory_service.get_memory(db, owner_id)
    if mem is None:
        return None
    parts = []
    if mem.user_profile:
        parts.append(f"[用户画像]\n{mem.user_profile}")
    if mem.soul:
        parts.append(f"[个性设定]\n{mem.soul}")
    if mem.notes:
        parts.append(f"[用户备忘]\n{mem.notes}")
    if not parts:
        return None
    return (
        "【用户长期记忆】请在对话中自然地参考以下信息，不要向用户复述这段内容：\n"
        + "\n\n".join(parts)
    )


_EPISODIC_PER_ITEM = 300     # chars per retrieved episode summary
_EPISODIC_LIMIT = 3          # max episodes injected per turn
_SKILLS_LIMIT = 2            # max skills injected per turn — see search_skills


async def _build_episodic_memory_prompt(
    db: AsyncSession, owner_id: uuid.UUID | None, query_text: str,
) -> str | None:
    """Retrieve pg_trgm-matched episode summaries and format as a system-prompt
    section. Unlike _build_knowledge_prompt (curated static docs, concatenated
    verbatim), this injects only already-LLM-summarized text — retrieved
    episodes are noisy/uncurated by nature, so raw concatenation isn't safe here.
    """
    if not owner_id:
        return None
    from app.services import memory_service

    episodes = await memory_service.search_episodes(db, owner_id, query_text, limit=_EPISODIC_LIMIT)
    if not episodes:
        return None
    parts = [f"- 「{e.title}」{e.summary[:_EPISODIC_PER_ITEM]}" for e in episodes]
    return (
        "【历史对话回忆】以下是与本次话题相关的过往对话摘要，供参考，不要向用户复述这段内容：\n"
        + "\n".join(parts)
    )


async def _build_skills_prompt(
    db: AsyncSession, profile: Profile | None, owner_id: uuid.UUID | None, query_text: str,
) -> tuple[str | None, list[uuid.UUID]]:
    """Trigger-match this profile/user's skills against the incoming message
    and inject only the matched ones — skills load into context on demand,
    unlike the always-on Profile.system_prompt/knowledge bindings.

    Returns (prompt_text_or_None, matched_skill_ids) — the caller threads the
    id list through to the runner so it can record a SkillFiring per id once
    the turn completes (see runner.py's handle_single), feeding the
    self-evolving-skills eval-dataset builder.
    """
    from app.services import memory_service

    skills = await memory_service.search_skills(
        db,
        profile_id=profile.id if profile else None,
        owner_id=owner_id,
        team_id=profile.team_id if profile else None,
        query=query_text,
        limit=_SKILLS_LIMIT,
    )
    if not skills:
        return None, []
    parts = [f"[技能：{s.name}]\n{s.content}" for s in skills]
    prompt = "【已触发技能】\n" + "\n\n".join(parts)
    return prompt, [s.id for s in skills]


_KNOWLEDGE_PER_ITEM = 2000   # chars per bound knowledge entry
_KNOWLEDGE_TOTAL = 8000      # chars across all entries


async def _collect_folder_knowledge_ids(
    db: AsyncSession, folder_ids: list[str]
) -> list[uuid.UUID]:
    """Recursively collect all non-folder item IDs under the given folder IDs.

    Works for both TeamKnowledge and ProjectDoc tables.
    """
    from app.db.models.team import TeamKnowledge, ProjectDoc

    result: list[uuid.UUID] = []
    seen_folders: set[uuid.UUID] = set()

    async def _recurse(fids: list[uuid.UUID]):
        for fid in fids:
            if fid in seen_folders:
                continue
            seen_folders.add(fid)
            # TeamKnowledge children
            tk_rows = (await db.execute(
                select(TeamKnowledge).where(
                    TeamKnowledge.folder_id == fid,
                    TeamKnowledge.is_folder.is_(False),
                )
            )).scalars().all()
            for r in tk_rows:
                result.append(r.id)
            # ProjectDoc children
            pd_rows = (await db.execute(
                select(ProjectDoc).where(
                    ProjectDoc.folder_id == fid,
                    ProjectDoc.is_folder.is_(False),
                )
            )).scalars().all()
            for r in pd_rows:
                result.append(r.id)
            # Recurse into sub-folders (both tables)
            tk_sub = (await db.execute(
                select(TeamKnowledge.id).where(
                    TeamKnowledge.folder_id == fid,
                    TeamKnowledge.is_folder.is_(True),
                )
            )).scalars().all()
            pd_sub = (await db.execute(
                select(ProjectDoc.id).where(
                    ProjectDoc.folder_id == fid,
                    ProjectDoc.is_folder.is_(True),
                )
            )).scalars().all()
            await _recurse(list(tk_sub) + list(pd_sub))

    valid_ids = []
    for fid_str in folder_ids:
        try:
            valid_ids.append(uuid.UUID(str(fid_str)))
        except (ValueError, TypeError):
            continue
    await _recurse(valid_ids)
    return result


async def _build_knowledge_prompt(db: AsyncSession, profile: Profile | None) -> str | None:
    """Inject the content of a Profile's bound knowledge into the prompt.

    Supports:
    - knowledge_ids: individual item IDs (backward compat)
    - knowledge_folder_ids: folder IDs — all items under these folders are injected
    - knowledge_team_ids: team IDs — every non-folder item under these teams is injected
    """
    if not profile:
        return None
    from app.db.models.team import ProjectDoc, TeamKnowledge

    # Collect all knowledge IDs to load
    all_ids: list[uuid.UUID] = []

    # 1. Direct item bindings (backward compat)
    for kid in (getattr(profile, "knowledge_ids", None) or []):
        try:
            all_ids.append(uuid.UUID(str(kid)))
        except (ValueError, TypeError):
            continue

    # 2. Folder bindings — recursively collect all items under folders
    folder_ids = getattr(profile, "knowledge_folder_ids", None) or []
    if folder_ids:
        folder_item_ids = await _collect_folder_knowledge_ids(db, folder_ids)
        all_ids.extend(folder_item_ids)

    # 3. Whole-team bindings — every non-folder item under these teams
    team_ids = getattr(profile, "knowledge_team_ids", None) or []
    if team_ids:
        valid_team_ids: list[uuid.UUID] = []
        for tid in team_ids:
            try:
                valid_team_ids.append(uuid.UUID(str(tid)))
            except (ValueError, TypeError):
                continue
        if valid_team_ids:
            team_rows = (await db.execute(
                select(TeamKnowledge.id).where(
                    TeamKnowledge.team_id.in_(valid_team_ids),
                    TeamKnowledge.is_folder.is_(False),
                )
            )).scalars().all()
            all_ids.extend(team_rows)

    # An item may be reachable via more than one binding (direct + folder + team) —
    # dedup while preserving first-seen order so it isn't injected multiple times.
    all_ids = list(dict.fromkeys(all_ids))

    if not all_ids:
        return None

    parts: list[str] = []
    used = 0
    for kid in all_ids:
        entry = await db.get(TeamKnowledge, kid) or await db.get(ProjectDoc, kid)
        if entry is None or not getattr(entry, "content", None):
            continue
        body = entry.content[:_KNOWLEDGE_PER_ITEM]
        if used + len(body) > _KNOWLEDGE_TOTAL:
            body = body[: max(0, _KNOWLEDGE_TOTAL - used)]
        if not body:
            break
        parts.append(f"[{entry.name}]\n{body}")
        used += len(body)
        if used >= _KNOWLEDGE_TOTAL:
            break
    if not parts:
        return None
    return (
        "【团队知识库】请在回答时参考以下资料，不要向用户复述这段说明：\n"
        + "\n\n".join(parts)
    )


async def dispatch(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    skip_agent: bool = False,
    profile_id_override: str | None = None,
    task_id: uuid.UUID | None = None,
) -> tuple[Message, Message | None]:
    """Route to single or roundtable based on the conversation's active agents."""
    agents = list(convo.active_agent_ids or [convo.primary_agent_id])
    if skip_agent:
        return await send_user_only(
            db, convo, text, attached_file_ids=attached_file_ids, owner_id=owner_id, task_id=task_id
        )

    # Load profile system_prompt — request-level override wins over conversation default
    system_prompt: str | None = None
    profile_dir: str | None = None
    mcp_servers: list[dict] = []
    profile: Profile | None = None
    effective_profile_id = profile_id_override or convo.profile_id
    if effective_profile_id:
        profile = await db.get(Profile, effective_profile_id)
        if profile:
            system_prompt = profile.system_prompt or None
            profile_dir = _profile_dir(profile)
            mcp_servers = await _resolve_mcp_servers(db, profile)
            # Inject the Profile's bound knowledge-base content (reuse loop)
            knowledge_prompt = await _build_knowledge_prompt(db, profile)
            if knowledge_prompt:
                system_prompt = (
                    f"{system_prompt}\n\n{knowledge_prompt}" if system_prompt else knowledge_prompt
                )

    # Inject the user's long-term agent memory into system_prompt
    memory_prompt = await _build_memory_prompt(db, owner_id)
    if memory_prompt:
        system_prompt = f"{system_prompt}\n\n{memory_prompt}" if system_prompt else memory_prompt

    # Layered memory: pg_trgm-retrieved episodic summaries + trigger-matched
    # skills, both injected only when relevant (unlike the always-on blocks
    # above). Gated by a kill switch in case retrieval quality misbehaves.
    matched_skill_ids: list[uuid.UUID] = []
    if settings.memory_episodic_injection_enabled:
        episodic_prompt = await _build_episodic_memory_prompt(db, owner_id, text)
        if episodic_prompt:
            system_prompt = f"{system_prompt}\n\n{episodic_prompt}" if system_prompt else episodic_prompt
        skills_prompt, matched_skill_ids = await _build_skills_prompt(db, profile, owner_id, text)
        if skills_prompt:
            system_prompt = f"{system_prompt}\n\n{skills_prompt}" if system_prompt else skills_prompt

    # MoA ("mixture of agents"): the selected Profile fans the message out to
    # its bound reference profiles via the same roundtable executor used for
    # multi-agent compare/group roundtable, then synthesizes one reply — no
    # new merge logic, just a different way of building `targets`.
    if profile is not None and profile.is_moa and profile.moa_target_profile_ids:
        moa_targets = await _build_moa_targets(db, profile.moa_target_profile_ids, memory_prompt)
        if moa_targets:
            return await send_roundtable(
                db, convo, text, moa_targets,
                attached_file_ids=attached_file_ids, owner_id=owner_id, task_id=task_id, moa=True,
            )

    # Anti-clarify guidance only on follow-up turns — the first turn carries the
    # clarify preamble, and sending both contradicted each other.
    if convo.acp_session_id and len(agents) == 1:
        system_prompt = f"{system_prompt}\n\n{_ANTI_CLARIFY}" if system_prompt else _ANTI_CLARIFY

    if len(agents) > 1:
        # Personal multi-agent compare: same persona/profile broadcast to every
        # underlying CLI agent (unlike group-chat roundtable, there's no
        # per-member Profile here — active_agent_ids are bare agent ids).
        targets = [
            {
                "agent_id": aid, "profile_id": None, "system_prompt": system_prompt,
                "profile_dir": profile_dir, "mcp_servers": mcp_servers,
            }
            for aid in agents
        ]
        return await send_roundtable(
            db, convo, text, targets,
            attached_file_ids=attached_file_ids, owner_id=owner_id, task_id=task_id,
        )
    return await send_message(
        db, convo, text,
        attached_file_ids=attached_file_ids, owner_id=owner_id,
        system_prompt=system_prompt, profile_dir=profile_dir, mcp_servers=mcp_servers,
        task_id=task_id, profile_id=effective_profile_id, matched_skill_ids=matched_skill_ids,
    )


async def set_active_agents(
    db: AsyncSession, convo: Conversation, agent_ids: list[str]
) -> Conversation:
    convo.active_agent_ids = agent_ids or ["hermes"]
    convo.primary_agent_id = convo.active_agent_ids[0]
    await db.commit()
    await db.refresh(convo)
    return convo


async def list_files(db: AsyncSession, conversation_id: uuid.UUID) -> list[WorkspaceFile]:
    res = await db.execute(
        select(WorkspaceFile)
        .where(WorkspaceFile.conversation_id == conversation_id)
        .order_by(WorkspaceFile.updated_at.desc())
    )
    return list(res.scalars().all())


async def delete_conversation(db: AsyncSession, convo: Conversation) -> None:
    await db.delete(convo)
    await db.commit()
    # Invalidate conversation list cache


async def fork_conversation(
    db: AsyncSession,
    source_id: uuid.UUID,
    owner_id: uuid.UUID,
    before_message_id: uuid.UUID,
) -> tuple[Conversation, list[Message]]:
    """Deep-copy a conversation up to and including a given message."""
    source = await get_conversation(db, source_id, owner_id)
    if not source:
        raise ValueError("conversation not found")
    all_msgs = await get_messages(db, source_id, limit=500)  # limit to prevent OOM on long conversations
    cut = next((i for i, m in enumerate(all_msgs) if m.id == before_message_id), len(all_msgs) - 1)
    fork = Conversation(
        owner_id=owner_id,
        title=f"[分支] {source.title}",
        primary_agent_id=source.primary_agent_id,
        profile_id=source.profile_id,
        team_id=source.team_id,
        project_id=source.project_id,
    )
    db.add(fork)
    await db.flush()
    copied_msgs = []
    for m in all_msgs[: cut + 1]:
        nm = Message(
            conversation_id=fork.id,
            owner_id=m.owner_id,
            role=m.role,
            agent_id=m.agent_id,
            content=m.content,
            status=m.status,
        )
        db.add(nm)
        copied_msgs.append(nm)
    await db.commit()
    await db.refresh(fork)
    return fork, copied_msgs


async def update_file_content(
    db: AsyncSession, f: WorkspaceFile, content: str, author: str | None = None
) -> WorkspaceFile:
    # Re-fetch with a row lock to serialize concurrent writers (e.g. a
    # roundtable agent writing the same file while a user saves an edit).
    # populate_existing is required: callers already hold `f` loaded from an
    # earlier db.get() in the same session, so without it SQLAlchemy would
    # keep serving the stale, already-identity-mapped attributes instead of
    # the fresh (lock-acquired) row — silently defeating the lock.
    res = await db.execute(
        select(WorkspaceFile)
        .where(WorkspaceFile.id == f.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    f = res.scalar_one()
    # Save current version before overwriting.
    # For MinIO storage, f.content may be None — read from object storage
    old_content = f.content
    if old_content is None and f.storage_key:
        from app.core import object_storage
        import asyncio
        try:
            raw = await asyncio.to_thread(object_storage.get, f.storage_key)
            old_content = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
        except Exception:
            old_content = None
    if old_content:
        ver = WorkspaceFileVersion(
            file_id=f.id,
            version_num=f.current_version,
            content=old_content,
            size_bytes=f.size_bytes,
            author=author,
        )
        db.add(ver)
    # Keep only the latest 10 versions
    old_versions = (
        await db.execute(
            select(WorkspaceFileVersion)
            .where(WorkspaceFileVersion.file_id == f.id)
            .order_by(WorkspaceFileVersion.version_num.desc())
            .offset(9)  # Keep 10 (0-9), delete from 10th onwards
        )
    ).scalars().all()
    for old in old_versions:
        await db.delete(old)
    f.content = content
    f.size_bytes = len(content.encode("utf-8"))
    f.current_version += 1
    await db.commit()
    await db.refresh(f)
    return f


async def list_file_versions(
    db: AsyncSession, file_id: uuid.UUID
) -> list[WorkspaceFileVersion]:
    res = await db.execute(
        select(WorkspaceFileVersion)
        .where(WorkspaceFileVersion.file_id == file_id)
        .order_by(WorkspaceFileVersion.version_num.desc())
    )
    return list(res.scalars().all())


async def restore_file_version(
    db: AsyncSession, f: WorkspaceFile, version_num: int, author: str | None = None
) -> WorkspaceFile:
    res = await db.execute(
        select(WorkspaceFileVersion).where(
            WorkspaceFileVersion.file_id == f.id,
            WorkspaceFileVersion.version_num == version_num,
        )
    )
    ver = res.scalar_one_or_none()
    if ver is None:
        return f
    return await update_file_content(db, f, ver.content or "", author=author)


# ── Group chat service functions ──────────────────────────────────────────


async def create_group(
    db: AsyncSession,
    owner_id: uuid.UUID,
    *,
    title: str,
    member_user_ids: list[uuid.UUID] | None = None,
    member_agent_ids: list[str] | None = None,
    team_id: uuid.UUID | None = None,
) -> Conversation:
    """创建群聊，自动添加成员。有团队时默认包含全部成员+助手。"""
    from app.db.models.team import Team, TeamMember as TM

    # If team_id, auto-populate members + agents from team
    channel_mode = "mention"
    # (profile_id, agent_id) — profile_id is None when there's no backing
    # Profile row (e.g. no shared profiles configured for the team yet).
    profile_pairs: list[tuple[str | None, str]] = []
    if team_id:
        team = await db.get(Team, team_id)
        if team:
            channel_mode = team.channel_mode or "mention"
            # Auto-add all team human members (when not explicitly provided)
            if not member_user_ids:
                res = await db.execute(
                    select(TM.user_id).where(TM.team_id == team_id)
                )
                member_user_ids = [row[0] for row in res.all()]
            # Auto-add team shared profiles (resolve from shared_profile_ids).
            if not member_agent_ids:
                profile_pairs = await _resolve_team_profiles(db, team)

    # Fallback: if no profiles resolved and no explicit agents, use default.
    if not profile_pairs and not member_agent_ids:
        profile_pairs = [(None, "hermes")]

    # If explicit agent_ids were passed (no profiles), convert to pairs.
    if not profile_pairs and member_agent_ids:
        profile_pairs = [(None, aid) for aid in member_agent_ids]

    agent_ids = [aid for _pid, aid in profile_pairs]
    profile_ids = [pid for pid, _aid in profile_pairs if pid]

    convo = Conversation(
        title=title,
        owner_id=owner_id,
        type="group",
        primary_agent_id=agent_ids[0] if agent_ids else "hermes",
        active_agent_ids=agent_ids,
        active_profile_ids=profile_ids,
        team_id=team_id,
        channel_mode=channel_mode,
        visibility="private" if not team_id else "team",
    )
    db.add(convo)
    await db.flush()  # get convo.id

    # Add creator as admin
    db.add(GroupMember(
        conversation_id=convo.id,
        user_id=owner_id,
        role="admin",
    ))

    # Add other human members (dedupe against owner)
    added_users = {owner_id}
    for uid in (member_user_ids or []):
        if uid not in added_users:
            added_users.add(uid)
            db.add(GroupMember(
                conversation_id=convo.id,
                user_id=uid,
                role="member",
            ))

    # Add agent members (with profile_id when a real Profile backs this agent)
    for pid, aid in profile_pairs:
        gm = GroupMember(
            conversation_id=convo.id,
            agent_id=aid,
            role="member",
        )
        if pid:
            gm.profile_id = uuid.UUID(pid)
        db.add(gm)

    await db.commit()
    await db.refresh(convo)
    return convo


async def get_group_members(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> list:
    """获取群聊成员列表。"""
    res = await db.execute(
        select(GroupMember)
        .where(GroupMember.conversation_id == conversation_id)
        .order_by(GroupMember.joined_at)
    )
    return list(res.scalars().all())


async def add_group_member(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    agent_id: str | None = None,
    role: str = "member",
):
    """添加群聊成员。"""
    if not user_id and not agent_id:
        raise ValueError("必须指定 user_id 或 agent_id")

    # Check if already exists
    existing = await db.execute(
        select(GroupMember).where(
            GroupMember.conversation_id == conversation_id,
            GroupMember.user_id == user_id,
            GroupMember.agent_id == agent_id,
        )
    )
    if existing.scalar_one_or_none():
        return  # already a member

    member = GroupMember(
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=agent_id,
        role=role,
    )
    db.add(member)

    # Update active_agent_ids if it's an agent
    if agent_id:
        convo = await db.get(Conversation, conversation_id)
        if convo and agent_id not in (convo.active_agent_ids or []):
            convo.active_agent_ids = (convo.active_agent_ids or []) + [agent_id]

    await db.commit()


async def remove_group_member(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    agent_id: str | None = None,
):
    """移除群聊成员。"""
    stmt = select(GroupMember).where(
        GroupMember.conversation_id == conversation_id,
    )
    if user_id:
        stmt = stmt.where(GroupMember.user_id == user_id)
    elif agent_id:
        stmt = stmt.where(GroupMember.agent_id == agent_id)
    else:
        return

    res = await db.execute(stmt)
    member = res.scalar_one_or_none()
    if member:
        await db.delete(member)

        # Update active_agent_ids if it's an agent
        if agent_id:
            convo = await db.get(Conversation, conversation_id)
            if convo:
                convo.active_agent_ids = [
                    a for a in (convo.active_agent_ids or []) if a != agent_id
                ]

        await db.commit()


async def list_group_conversations(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[Conversation]:
    """列出用户参与的所有群聊。"""
    stmt = (
        select(Conversation)
        .join(GroupMember, GroupMember.conversation_id == Conversation.id)
        .where(
            Conversation.type == "group",
            GroupMember.user_id == user_id,
        )
        .order_by(Conversation.updated_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def is_group_member(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """检查用户是否是群聊成员。"""
    res = await db.execute(
        select(GroupMember).where(
            GroupMember.conversation_id == conversation_id,
            GroupMember.user_id == user_id,
        )
    )
    return res.scalar_one_or_none() is not None


async def is_group_admin(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """检查用户是否是群聊管理员（群主）。"""
    res = await db.execute(
        select(GroupMember).where(
            GroupMember.conversation_id == conversation_id,
            GroupMember.user_id == user_id,
            GroupMember.role == "admin",
        )
    )
    return res.scalar_one_or_none() is not None


@dataclass
class ResolvedMentions:
    """@提及解析结果：Agent 桶 + 人类桶 + 全体标记。

    `agent_ids` is a deduped, bare-agent-id view kept for backward
    compatibility (tests, simple display). `agent_targets` is the real
    routing list — one (profile_id, agent_id) pair per distinct GroupMember,
    NEVER deduped by agent_id, since multiple Profiles can share one
    underlying CLI agent (e.g. two personas both built on "claude").
    """
    agent_ids: list[str] = field(default_factory=list)
    agent_targets: list[tuple[str | None, str]] = field(default_factory=list)
    user_ids: list[uuid.UUID] = field(default_factory=list)
    all_humans: bool = False
    all_agents: bool = False


async def resolve_mentions(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    mentions: list[str],
) -> ResolvedMentions:
    """把前端发来的稳定 ID 解析成结构化结果。

    前端已发送稳定标识，无需再做名称模糊匹配：
    - "__all_agents__" → 群内全部 Agent（每个 Profile 独立一份，即使共用 agent_id）
    - "__all_humans__" → all_humans 标记（仅通知，不触发 AI）
    - "user:{uuid}"    → 人类成员（校验确为成员）
    - "profile:{uuid}" → 精确指定某个 Profile 成员
    - 裸 agent id       → Agent（校验确在群内；命中该 agent_id 下的全部 Profile）
    """
    out = ResolvedMentions()
    if not mentions:
        return out

    members = await get_group_members(db, conversation_id)
    ai_members = [m for m in members if m.agent_id]
    group_agents = [m.agent_id for m in ai_members]
    group_user_ids = {str(m.user_id) for m in members if m.user_id}

    def _add_member(m) -> None:
        key = (str(m.profile_id) if m.profile_id else None, m.agent_id)
        if key not in out.agent_targets:
            out.agent_targets.append(key)

    if "__all_agents__" in mentions:
        out.all_agents = True
        out.agent_ids = list(dict.fromkeys(group_agents))
        for m in ai_members:
            _add_member(m)
    if "__all_humans__" in mentions:
        out.all_humans = True

    for mention in mentions:
        if mention in ("__all_agents__", "__all_humans__"):
            continue
        if mention.startswith("user:"):
            uid_str = mention.split(":", 1)[1]
            if uid_str in group_user_ids:
                try:
                    uid = uuid.UUID(uid_str)
                except ValueError:
                    continue
                if uid not in out.user_ids:
                    out.user_ids.append(uid)
            continue
        if mention.startswith("profile:"):
            pid_str = mention.split(":", 1)[1]
            member = next(
                (m for m in ai_members if m.profile_id and str(m.profile_id) == pid_str), None
            )
            if member:
                _add_member(member)
                if member.agent_id not in out.agent_ids:
                    out.agent_ids.append(member.agent_id)
            continue
        # Bare agent id — trust it only if it is actually a group agent. All
        # Profiles sharing this agent_id are targeted (no way to disambiguate
        # a single one from a bare id, so address them all rather than an
        # arbitrary pick).
        if mention in group_agents:
            if mention not in out.agent_ids:
                out.agent_ids.append(mention)
            for m in ai_members:
                if m.agent_id == mention:
                    _add_member(m)

    return out


# ── Realtime fan-out helpers ───────────────────────────────────────────────


async def _build_reply_ref(db: AsyncSession, reply_to_id: uuid.UUID | None) -> dict | None:
    """被引用消息的精简摘要（用于渲染回复引用条）。"""
    if not reply_to_id:
        return None
    ref = await db.get(Message, reply_to_id)
    if ref is None:
        return None
    snippet = (ref.content or {}).get("text") or ""
    return {
        "id": str(ref.id),
        "role": ref.role,
        "owner_id": str(ref.owner_id) if ref.owner_id else None,
        "agent_id": ref.agent_id,
        "snippet": snippet[:80],
    }


async def message_to_event_dict(db: AsyncSession, msg: Message) -> dict:
    """把 Message ORM 转成 MessageOut 形状的纯 dict，供实时事件使用。"""
    return {
        "id": str(msg.id),
        "conversation_id": str(msg.conversation_id),
        "owner_id": str(msg.owner_id) if msg.owner_id else None,
        "role": msg.role,
        "agent_id": msg.agent_id,
        "profile_id": str(msg.profile_id) if msg.profile_id else None,
        "content": msg.content or {},
        "status": msg.status,
        "mentions": msg.mentions or [],
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "reply_to_id": str(msg.reply_to_id) if msg.reply_to_id else None,
        "reply_to": await _build_reply_ref(db, msg.reply_to_id),
        "task_id": str(msg.task_id) if msg.task_id else None,
        "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
        "deleted_at": msg.deleted_at.isoformat() if msg.deleted_at else None,
        "reactions": msg.reactions or {},
    }


async def _publish_user_message(
    db: AsyncSession,
    convo: Conversation,
    user_msg: Message,
    resolved: ResolvedMentions,
) -> None:
    """广播人类消息给会话流（含发送者回显），并对被通知的人类成员推送 notify。"""
    msg_dict = await message_to_event_dict(db, user_msg)
    await redis_core.publish_event(str(convo.id), {"type": "message", "message": msg_dict})

    members = await get_group_members(db, convo.id)
    snippet = (user_msg.content or {}).get("text", "")[:80]
    mentioned_ids = set(resolved.user_ids)
    r = redis_core.get_redis()
    pipe = r.pipeline()
    for m in members:
        if not m.user_id or m.user_id == user_msg.owner_id:
            continue
        mention = resolved.all_humans or (m.user_id in mentioned_ids)
        event = _json.dumps({
            "type": "notify",
            "conversation_id": str(convo.id),
            "title": convo.title,
            "snippet": snippet,
            "mention": bool(mention),
        })
        uid = str(m.user_id)
        pipe.xadd(redis_core.user_stream(uid), {"data": event}, maxlen=redis_core.USER_STREAM_MAXLEN, approximate=True)
        pipe.expire(redis_core.user_stream(uid), redis_core.USER_STREAM_TTL)
    if len(pipe):
        await pipe.execute()


async def _persist_group_user_msg(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    mentions: list[str],
    owner_id: uuid.UUID | None,
    *,
    attached_file_ids: list[str] | None = None,
    reply_to_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> Message:
    """持久化群聊用户消息（统一入口，附带文件 + 回复引用）。"""
    attached = await _resolve_attached_files(db, attached_file_ids or [], conversation_id=str(convo.id))
    content: dict = {"text": text}
    if attached:
        content["files"] = attached
    user_msg = Message(
        conversation_id=convo.id,
        owner_id=owner_id,
        role="user",
        content=content,
        mentions=mentions or [],
        reply_to_id=reply_to_id,
        status="complete",
        task_id=task_id,
    )
    db.add(user_msg)
    if convo.title == "新会话":
        convo.title = text[:40]
    await db.commit()
    await db.refresh(user_msg)
    return user_msg


async def dispatch_group(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    mentions: list[str],
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    skip_agent: bool = False,
    profile_id_override: str | None = None,
    reply_to_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> tuple[Message, Message | None]:
    """群聊消息路由：按 channel_mode + mentions 决定走人→人 / 人→机 / 圆桌。

    无论是否触发 AI，人类消息总是先持久化并实时广播给全体成员（含发送者回显）。
    """
    resolved = await resolve_mentions(db, convo.id, mentions)
    mode = getattr(convo, "channel_mode", "mention") or "mention"

    # 计算 Agent 目标：(profile_id, agent_id) 二元组，绝不按 agent_id 去重——
    # 多个 Profile 可能共用同一个底层 agent_id（例如两个基于同一 CLI 的人设）。
    agent_targets: list[tuple[str | None, str]] = list(resolved.agent_targets)
    if mode == "always" and not agent_targets and not resolved.all_humans and not skip_agent:
        members = await get_group_members(db, convo.id)
        agent_targets = [
            (str(m.profile_id) if m.profile_id else None, m.agent_id)
            for m in members if m.agent_id
        ]

    save_only = (
        mode == "off" or skip_agent or resolved.all_humans or not agent_targets
    )

    # 始终先落库 + 广播人类消息
    user_msg = await _persist_group_user_msg(
        db, convo, text, mentions, owner_id,
        attached_file_ids=attached_file_ids, reply_to_id=reply_to_id, task_id=task_id,
    )
    await _publish_user_message(db, convo, user_msg, resolved)

    if save_only:
        return user_msg, None

    if len(agent_targets) == 1:
        # 人→机模式：单 Agent
        target_profile_id, target_agent_id = agent_targets[0]
        system_prompt = None
        profile_dir = None
        mcp_servers: list[dict] = []
        # 优先级：显式 profile_id_override > 该成员绑定的 Profile > 会话默认
        effective_profile_id = profile_id_override or target_profile_id or convo.profile_id
        if not profile_id_override and not target_profile_id:
            # 该成员没有绑定具体 Profile（历史数据）：退化为按 agent_id 尽力匹配
            res_p = await db.execute(
                select(Profile).where(
                    Profile.default_agent_id == target_agent_id,
                    Profile.is_active.is_(True),
                ).limit(1)
            )
            agent_profile = res_p.scalars().first()
            if agent_profile:
                effective_profile_id = str(agent_profile.id)
        if effective_profile_id:
            profile = await db.get(Profile, effective_profile_id)
            if profile:
                system_prompt = profile.system_prompt or None
                profile_dir = _profile_dir(profile)
                mcp_servers = await _resolve_mcp_servers(db, profile)

        memory_prompt = await _build_memory_prompt(db, owner_id)
        if memory_prompt:
            system_prompt = f"{system_prompt}\n\n{memory_prompt}" if system_prompt else memory_prompt
        if convo.acp_session_id:
            system_prompt = f"{system_prompt}\n\n{_ANTI_CLARIFY}" if system_prompt else _ANTI_CLARIFY

        # Attribute the reply to the @-mentioned agent/profile without mutating
        # the conversation's stable primary_agent_id default.
        _, agent_msg = await send_message(
            db, convo, text,
            attached_file_ids=attached_file_ids,
            owner_id=owner_id,
            system_prompt=system_prompt,
            existing_user_msg=user_msg,
            profile_dir=profile_dir,
            mcp_servers=mcp_servers,
            agent_id_override=target_agent_id,
            profile_id=effective_profile_id,
        )
        return user_msg, agent_msg

    # 圆桌模式：多 Agent 并行 — 每个目标独立解析自己绑定的 Profile 人设/工作目录，
    # 不再用单一会话级 Profile 覆盖全部参与者（这正是此前"圆桌形同虚设"的根因）。
    memory_prompt = await _build_memory_prompt(db, owner_id)
    targets: list[dict] = []
    for target_profile_id, target_agent_id in agent_targets:
        t_system_prompt = None
        t_profile_dir = None
        t_mcp_servers: list[dict] = []
        eff_pid = target_profile_id or convo.profile_id
        if eff_pid:
            profile = await db.get(Profile, eff_pid)
            if profile:
                t_system_prompt = profile.system_prompt or None
                t_profile_dir = _profile_dir(profile)
                t_mcp_servers = await _resolve_mcp_servers(db, profile)
        if memory_prompt:
            t_system_prompt = f"{t_system_prompt}\n\n{memory_prompt}" if t_system_prompt else memory_prompt
        targets.append({
            "agent_id": target_agent_id,
            "profile_id": target_profile_id,
            "system_prompt": t_system_prompt,
            "profile_dir": t_profile_dir,
            "mcp_servers": t_mcp_servers,
        })

    return await send_roundtable(
        db, convo, text, targets,
        attached_file_ids=attached_file_ids,
        owner_id=owner_id,
        mentions=mentions,
        existing_user_msg=user_msg,
    )


# ── Read state · unread badges ─────────────────────────────────────────────


async def mark_read(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
):
    """把成员的已读游标推进到现在。"""
    res = await db.execute(
        select(GroupMember).where(
            GroupMember.conversation_id == conversation_id,
            GroupMember.user_id == user_id,
        )
    )
    gm = res.scalar_one_or_none()
    if gm is None:
        return None
    gm.last_read_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(gm)
    return gm.last_read_at


async def unread_summary(
    db: AsyncSession,
    user_id: uuid.UUID,
    conversation_ids: list[uuid.UUID],
) -> dict[str, dict]:
    """一次分组查询返回 {conv_id: {unread, mention}}。手工组装，避免懒加载。"""
    out: dict[str, dict] = {
        str(cid): {"unread": 0, "mention": False} for cid in conversation_ids
    }
    if not conversation_ids:
        return out

    gm = GroupMember
    mention_row = or_(
        Message.mentions.contains([f"user:{user_id}"]),
        Message.mentions.contains(["__all_humans__"]),
    )
    stmt = (
        select(
            Message.conversation_id,
            func.count(Message.id),
            func.bool_or(mention_row),
        )
        .select_from(Message)
        .join(
            gm,
            and_(
                gm.conversation_id == Message.conversation_id,
                gm.user_id == user_id,
            ),
        )
        .where(
            Message.conversation_id.in_(conversation_ids),
            or_(Message.owner_id.is_(None), Message.owner_id != user_id),
            or_(gm.last_read_at.is_(None), Message.created_at > gm.last_read_at),
        )
        .group_by(Message.conversation_id)
    )
    res = await db.execute(stmt)
    for conv_id, count, has_mention in res.all():
        out[str(conv_id)] = {"unread": int(count or 0), "mention": bool(has_mention)}
    return out


# ── Message edit · recall · reactions ──────────────────────────────────────


async def edit_message(db: AsyncSession, msg: Message, text: str) -> Message:
    """编辑（仅文本），打 edited_at 标记并广播。"""
    msg.content = {**(msg.content or {}), "text": text}
    msg.edited_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)
    await redis_core.publish_event(str(msg.conversation_id), {
        "type": "message_update",
        "message_id": str(msg.id),
        "patch": {
            "content": msg.content,
            "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
        },
    })
    return msg


async def recall_message(db: AsyncSession, msg: Message) -> Message:
    """撤回（软删），清空内容并广播墓碑。"""
    msg.deleted_at = datetime.now(timezone.utc)
    msg.content = {"text": ""}
    await db.commit()
    await db.refresh(msg)
    await redis_core.publish_event(str(msg.conversation_id), {
        "type": "message_update",
        "message_id": str(msg.id),
        "patch": {
            "deleted_at": msg.deleted_at.isoformat() if msg.deleted_at else None,
            "content": {"text": ""},
        },
    })
    return msg


async def toggle_reaction(
    db: AsyncSession, msg: Message, user_id: uuid.UUID, emoji: str
) -> Message:
    """切换某 emoji 的回应（重新赋值 JSONB 以触发脏检测），并广播聚合结果。"""
    reactions = {k: list(v) for k, v in (msg.reactions or {}).items()}
    uid = str(user_id)
    users = reactions.get(emoji, [])
    if uid in users:
        users.remove(uid)
    else:
        users.append(uid)
    if users:
        reactions[emoji] = users
    else:
        reactions.pop(emoji, None)
    msg.reactions = reactions
    await db.commit()
    await db.refresh(msg)
    await redis_core.publish_event(str(msg.conversation_id), {
        "type": "message_update",
        "message_id": str(msg.id),
        "patch": {"reactions": reactions},
    })
    return msg


# ── Canonical team / project groups ────────────────────────────────────────


async def _team_member_ids(db: AsyncSession, team_id: uuid.UUID) -> list[uuid.UUID]:
    from app.db.models.team import TeamMember as TM
    res = await db.execute(select(TM.user_id).where(TM.team_id == team_id))
    return [row[0] for row in res.all()]


async def _resolve_team_profiles(db: AsyncSession, team) -> list[tuple[str | None, str]]:
    """Resolve shared Profile ids → (profile_id, agent_id) pairs for team dispatch.

    Does NOT deduplicate by agent_id — each shared Profile is kept independently
    so that multiple profiles with the same default_agent_id are all included
    (they may have different system_prompt/model/icon/color).
    Falls back to [(None, "hermes")] when no profiles are shared — profile_id
    is None (not a fake UUID) since there's no real Profile row to reference;
    group_members.profile_id has an FK to profiles.id, so any non-null value
    here must resolve to an existing row.
    """
    pids_raw = [pid for pid in (team.shared_profile_ids or []) if pid]
    valid_uuids = []
    for pid in pids_raw:
        try:
            valid_uuids.append(uuid.UUID(pid))
        except (ValueError, TypeError):
            continue
    if not valid_uuids:
        return [(None, "hermes")]
    result = await db.execute(select(Profile).where(Profile.id.in_(valid_uuids)))
    pairs: list[tuple[str | None, str]] = []
    for p in result.scalars().all():
        if p.default_agent_id:
            pairs.append((str(p.id), p.default_agent_id))
    return pairs or [(None, "hermes")]


# Backward-compatible alias for callers that only need agent_ids.
async def _resolve_team_agents(db: AsyncSession, team) -> list[str]:
    pairs = await _resolve_team_profiles(db, team)
    return [agent_id for _pid, agent_id in pairs]


async def sync_group_membership(
    db: AsyncSession,
    convo: Conversation,
    *,
    human_user_ids: list[uuid.UUID],
    agent_ids: list[str],
    profile_pairs: list[tuple[str | None, str]] | None = None,
) -> None:
    """把群成员与团队/项目成员集做差量增删；admin 成员保留。

    When profile_pairs is provided, agent members are synced by profile_id
    (allows multiple profiles with the same agent_id).
    """
    members = await get_group_members(db, convo.id)
    existing_users = {m.user_id for m in members if m.user_id}
    existing_profile_ids = {m.profile_id for m in members if m.profile_id}
    # Agent members with no backing Profile row (e.g. bare "hermes" default) —
    # these can't be deduped by profile_id, so track/dedupe by agent_id instead.
    existing_noprofile_agents = {m.agent_id for m in members if m.agent_id and not m.profile_id}
    desired_users = set(human_user_ids)
    changed = False

    # Desired profile set (from pairs) + desired no-profile agent set
    desired_profile_set: set[uuid.UUID] = set()
    desired_noprofile_agents: set[str] = set()
    if profile_pairs:
        for pid, aid in profile_pairs:
            if pid:
                try:
                    desired_profile_set.add(uuid.UUID(pid))
                    continue
                except (ValueError, TypeError):
                    pass
            desired_noprofile_agents.add(aid)

    for uid in desired_users - existing_users:
        db.add(GroupMember(conversation_id=convo.id, user_id=uid, role="member"))
        changed = True

    # Add agent members — dedupe by profile_id when one is bound, else by agent_id
    if profile_pairs:
        for pid, aid in profile_pairs:
            puid: uuid.UUID | None = None
            if pid:
                try:
                    puid = uuid.UUID(pid)
                except (ValueError, TypeError):
                    puid = None
            if puid is not None:
                if puid in existing_profile_ids:
                    continue
                gm = GroupMember(conversation_id=convo.id, agent_id=aid, role="member")
                gm.profile_id = puid
                db.add(gm)
                changed = True
            else:
                if aid in existing_noprofile_agents:
                    continue
                db.add(GroupMember(conversation_id=convo.id, agent_id=aid, role="member"))
                existing_noprofile_agents.add(aid)  # avoid re-adding within this same pass
                changed = True
    else:
        existing_agent_keys = {m.agent_id for m in members if m.agent_id}
        desired_agent_keys = set(agent_ids)
        for aid in desired_agent_keys - existing_agent_keys:
            db.add(GroupMember(conversation_id=convo.id, agent_id=aid, role="member"))
            changed = True

    # Remove agent members no longer desired
    desired_agent_set = set(agent_ids)
    for m in members:
        if m.user_id and m.user_id not in desired_users and m.role != "admin":
            await db.delete(m)
            changed = True
        elif m.agent_id:
            if profile_pairs:
                if m.profile_id:
                    if m.profile_id not in desired_profile_set:
                        await db.delete(m)
                        changed = True
                elif m.agent_id not in desired_noprofile_agents:
                    await db.delete(m)
                    changed = True
            elif m.agent_id not in desired_agent_set:
                await db.delete(m)
                changed = True

    if changed:
        if agent_ids:
            convo.active_agent_ids = agent_ids
        if profile_pairs:
            convo.active_profile_ids = [pid for pid, _aid in profile_pairs if pid]
        await db.commit()
        await redis_core.publish_event(str(convo.id), {"type": "members_changed"})


async def get_or_create_team_group(
    db: AsyncSession, team, owner_id: uuid.UUID
) -> Conversation:
    """取或建团队的唯一固定群。

    用 is_channel=True 唯一标识"团队固定群"，与通过 /conversations/group 创建的
    临时群（同样带 team_id 但 is_channel=False）区分开。
    """
    res = await db.execute(
        select(Conversation).where(
            Conversation.team_id == team.id,
            Conversation.is_channel.is_(True),
        ).limit(1)
    )
    convo = res.scalar_one_or_none()
    if convo is not None and convo.type != "group":
        # 收编历史 is_channel 频道，统一到 group 模型
        convo.type = "group"
        convo.visibility = "team"
        await db.flush()

    human_ids = await _team_member_ids(db, team.id)
    if owner_id not in human_ids:
        human_ids.append(owner_id)
    pairs = await _resolve_team_profiles(db, team)
    agents = [aid for _pid, aid in pairs]
    profile_ids = [pid for pid, _aid in pairs if pid]

    if convo is None:
        convo = Conversation(
            title=f"{team.name} · 群聊",
            owner_id=owner_id,
            type="group",
            primary_agent_id=agents[0] if agents else "hermes",
            active_agent_ids=agents,
            active_profile_ids=profile_ids,
            team_id=team.id,
            channel_mode=team.channel_mode or "mention",
            visibility="team",
            is_channel=True,
        )
        db.add(convo)
        await db.flush()
        db.add(GroupMember(conversation_id=convo.id, user_id=owner_id, role="admin"))
        await db.commit()
        await db.refresh(convo)

    await sync_group_membership(db, convo, human_user_ids=human_ids, agent_ids=agents, profile_pairs=pairs)
    return convo


async def get_or_create_project_group(
    db: AsyncSession, project, owner_id: uuid.UUID
) -> Conversation:
    """取或建项目的唯一固定群。成员取 project.member_ids，Agent 取 pinned_agents。"""
    res = await db.execute(
        select(Conversation).where(
            Conversation.project_id == project.id,
            Conversation.type == "group",
        ).limit(1)
    )
    convo = res.scalar_one_or_none()

    human_ids: list[uuid.UUID] = []
    for x in (project.member_ids or []):
        try:
            human_ids.append(uuid.UUID(str(x)))
        except (ValueError, TypeError):
            continue
    if owner_id not in human_ids:
        human_ids.append(owner_id)
    # Resolve pinned Profile ids → (profile_id, agent_id) pairs for project dispatch.
    pairs: list[tuple[str | None, str]] = []
    for pid in (project.pinned_profile_ids or []):
        try:
            p = await db.get(Profile, uuid.UUID(pid))
        except (ValueError, TypeError):
            continue
        if p and p.default_agent_id:
            pairs.append((str(p.id), p.default_agent_id))
    if not pairs:
        pairs = [(None, "hermes")]
    agents = [aid for _pid, aid in pairs]
    profile_ids = [pid for pid, _aid in pairs if pid]

    if convo is None:
        convo = Conversation(
            title=f"{project.name} · 群聊",
            owner_id=owner_id,
            type="group",
            primary_agent_id=agents[0] if agents else "hermes",
            active_agent_ids=agents,
            active_profile_ids=profile_ids,
            team_id=project.team_id,
            project_id=project.id,
            channel_mode="mention",
            visibility="team",
        )
        db.add(convo)
        await db.flush()
        db.add(GroupMember(conversation_id=convo.id, user_id=owner_id, role="admin"))
        await db.commit()
        await db.refresh(convo)

    await sync_group_membership(db, convo, human_user_ids=human_ids, agent_ids=agents, profile_pairs=pairs)
    return convo


# ── closed-loop: consolidate conversation output → knowledge / tasks ──
_TASK_LINE_RE = re.compile(r"^\s*(?:\d+[\.\)、]|\*|-|·|•)\s+(.+)$")


def _parse_task_lines(text: str) -> list[str]:
    """Extract bullet/numbered list items as candidate task titles (deduped, capped).

    Shared by the extract-items endpoint and auto_create_tasks_from_message.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        m = _TASK_LINE_RE.match(line.strip())
        if not m:
            continue
        title = m.group(1).strip()
        title = title.strip("*` ").rstrip(":：")
        if not (3 <= len(title) <= 120):
            continue
        key = title[:50].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(title)
        if len(out) >= 20:
            break
    return out


async def get_message(db: AsyncSession, message_id: uuid.UUID) -> Message | None:
    return await db.get(Message, message_id)


async def consolidate_message(
    db: AsyncSession,
    *,
    message: Message,
    target: str,
    name: str,
    actor,
    project=None,
    team=None,
):
    """Archive a message's text into a ProjectDoc or TeamKnowledge with source tracing."""
    from app.schemas.team import DocCreate, KnowledgeCreate
    from app.services import team_service

    text = (message.content or {}).get("text", "") or ""
    size = len(text.encode("utf-8"))

    if target == "project_doc":
        if project is None:
            raise ValueError("project required for project_doc target")
        payload = DocCreate(name=name, kind="doc", size_bytes=size, content=text)
        payload.source_conversation_id = message.conversation_id
        payload.source_message_id = message.id
        doc = await team_service.add_doc(db, project.id, payload, actor)
        await team_service.log_activity(
            db, project=project, actor=actor, kind="doc.created",
            summary=f"沉淀了会话产出为文档「{name}」",
            meta={"doc_id": str(doc.id), "source_message_id": str(message.id)},
        )
        await team_service.notify_project_members(
            db, project, title=project.name, snippet=f"新文档：{name}",
            actor_id=actor.id if actor else None,
        )
        return doc

    if target == "team_knowledge":
        if team is None:
            raise ValueError("team required for team_knowledge target")
        payload = KnowledgeCreate(name=name, kind="doc", size_bytes=size, content=text)
        k = await team_service.add_knowledge(db, team.id, payload, actor)
        k.source_conversation_id = message.conversation_id
        k.source_message_id = message.id
        await db.commit()
        await db.refresh(k)
        if project is not None:
            await team_service.log_activity(
                db, project=project, actor=actor, kind="knowledge.created",
                summary=f"沉淀了会话产出为团队知识「{name}」",
                meta={"knowledge_id": str(k.id), "source_message_id": str(message.id)},
            )
        return k

    raise ValueError(f"unknown consolidate target: {target}")


async def auto_create_tasks_from_message(
    db: AsyncSession, *, message: Message, project, actor
) -> list:
    """Parse a message's bullet list into ProjectTasks (source-traced) + log activity."""
    from app.schemas.team import TaskCreate
    from app.services import team_service

    text = (message.content or {}).get("text", "") or ""
    titles = _parse_task_lines(text)
    created = []
    for title in titles:
        payload = TaskCreate(title=title)
        payload.source_conversation_id = message.conversation_id
        payload.source_message_id = message.id
        task = await team_service.create_task(db, project.id, payload)
        created.append(task)
    if created:
        await team_service.recompute_progress(db, project)
        await db.commit()
        await team_service.log_activity(
            db, project=project, actor=actor, kind="task.derived",
            summary=f"从会话生成了 {len(created)} 个任务",
            meta={"count": len(created), "source_message_id": str(message.id)},
        )
        await team_service.notify_project_members(
            db, project, title=project.name,
            snippet=f"新增 {len(created)} 个任务", actor_id=actor.id if actor else None,
        )
    return created


async def detect_action_items(
    db: AsyncSession, conversation_id: uuid.UUID
) -> dict:
    """Send a special prompt to the agent asking it to extract action items
    from the conversation. Returns the agent's text response for the frontend
    to parse and confirm."""
    convo = await db.get(Conversation, conversation_id)
    if not convo:
        raise ValueError("conversation not found")

    msgs = await get_messages(db, conversation_id, limit=20)
    if not msgs:
        return {"text": "", "items": []}

    # Build a transcript of recent messages
    transcript = []
    for m in msgs:
        role = "用户" if m.role == "user" else "助手"
        text = (m.content or {}).get("text", "")
        if text:
            transcript.append(f"{role}: {text[:300]}")

    prompt = (
        "分析以上对话，提取所有待办事项、决策和截止日期。\n"
        "以 JSON 格式输出，格式如下：\n"
        '{"tasks":[{"title":"任务标题","priority":"normal","deadline":null}],"decisions":["决策1"]}\n'
        "只输出 JSON，不要加其他文字。如果没有待办，返回 {\"tasks\":[],\"decisions\":[]}"
    )

    # Use the conversation's primary agent to process
    agent_id = convo.primary_agent_id or "hermes"
    # For simplicity, we just return the transcript + prompt and let the frontend
    # send it as a regular message to get the AI response.
    return {
        "transcript": "\n".join(transcript),
        "prompt": prompt,
        "agent_id": agent_id,
    }


async def execute_task_with_profile(
    db: AsyncSession,
    task_id: uuid.UUID,
    profile_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> dict:
    """Execute a project task using the specified profile via the agent runner.

    Enqueues a 'task_execution' prompt that the runner picks up and executes.
    """
    from app.db.models.team import ProjectTask
    from app.core import redis as redis_core

    task = await db.get(ProjectTask, task_id)
    if not task:
        raise ValueError("task not found")

    profile = await db.get(Profile, profile_id)
    if not profile:
        raise ValueError("profile not found")

    prompt_text = f"请执行以下任务：\n标题：{task.title}\n描述：{task.description or '无'}"

    await redis_core.enqueue_prompt({
        "type": "task_execution",
        "task_id": str(task.id),
        "user_id": str(actor_id),
        "agent_id": profile.default_agent_id,
        "profile_id": str(profile.id),
        "prompt": prompt_text,
        "system_prompt": profile.system_prompt or "",
    })

    # Mark task as in-progress
    task.status = "doing"
    await db.commit()

    return {"status": "enqueued", "task_id": str(task_id), "profile": profile.name}
