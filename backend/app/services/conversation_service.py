"""Conversation + message persistence and the send→enqueue hot path."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json as _json

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import redis as redis_core
from app.core.files import confine_to_dir, safe_relative_path
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
    )
    if pinned_only:
        stmt = stmt.where(Conversation.pinned.is_(True))
    if q:
        # Use PostgreSQL full-text search for better performance on large datasets
        # Falls back to LIKE for simple prefix matching
        if len(q) >= 2:
            # Full-text search with tsvector
            tsquery = func.plainto_tsquery("simple", q)
            stmt = stmt.where(
                func.to_tsvector("simple", func.coalesce(Conversation.title, "")).match(
                    tsquery, postgresql_regconfig="simple"
                )
            )
        else:
            # Short queries: use case-insensitive prefix match
            stmt = stmt.where(Conversation.title.ilike(f"{q}%"))
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

    Returns [{id, name, kind, workspace_path, content, size_bytes, mime_type}].
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
                 "yaml", "yml", "toml", "sh", "bash", "log", "xml", "css", "diff", "patch"}

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
            file_content = f.content or ""
            ext = (f.kind or "").lower()
            mime = MIME_MAP.get(ext, "application/octet-stream")
            is_image = ext in IMAGE_EXTS
            is_text = ext in TEXT_EXTS

            # Write file content to workspace so agent can read it — confine
            # the (possibly agent-authored) name so it can't escape ws_dir.
            rel_name = safe_relative_path(f.name)
            if ws_dir and file_content and not is_image:
                fpath = confine_to_dir(ws_dir, rel_name)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(file_content)

            result.append({
                "id": str(f.id), "name": f.name, "kind": f.kind,
                "workspace_path": f"attachments/{rel_name}" if ws_dir and file_content else None,
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
) -> tuple[Message, None]:
    """Save a user message without triggering agent (for channel mention mode)."""
    user_msg = Message(
        conversation_id=convo.id,
        owner_id=owner_id,
        role="user",
        content={"text": text},
        status="complete",
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


_INLINE_LIMIT = 8000  # chars — files smaller than this are inlined in full


def _build_attached_prompt(text: str, attached: list[dict]) -> str:
    """Build prompt text with attached files.

    Small files (< _INLINE_LIMIT chars) are inlined in full.
    Large files get a reference only — the agent reads them via read_file tool
    using the workspace path already provided by resource_link blocks.
    This prevents prompt token overflow when users attach knowledge-base documents.
    """
    if not attached:
        return text
    parts = []
    for f in attached:
        if f.get("is_image"):
            parts.append(f"[图片附件: {f['name']}]")
        else:
            content = f.get("content", "")
            ws_path = f.get("workspace_path", "")
            if content and len(content) <= _INLINE_LIMIT:
                # Small file: inline in full
                parts.append(f"【附件: {f['name']}】\n```\n{content}\n```")
            elif content:
                # Large file: reference only — agent reads via read_file tool
                size_kb = len(content) / 1024
                ref = f"【附件: {f['name']}】（{size_kb:.0f}KB，内容过长不内嵌）"
                if ws_path:
                    ref += f"\n文件路径: {ws_path}\n请用 read_file 工具分段读取需要的部分。"
                parts.append(ref)
            else:
                parts.append(f"【附件: {f['name']}】（文件内容为空）")
    return f"{text}\n\n" + "\n\n".join(parts)


async def send_message(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    system_prompt: str | None = None,
    existing_user_msg: Message | None = None,
    profile_dir: str | None = None,
    agent_id_override: str | None = None,
) -> tuple[Message, Message]:
    """Persist the user turn + an empty streaming agent turn, then enqueue ACP work.

    The per-token hot path does NOT touch the DB — the runner streams events
    and writes the agent message once on completion. Pass existing_user_msg
    when the caller already persisted the user turn (group dispatch) to avoid
    a duplicate user row. Pass agent_id_override to attribute the reply to a
    specific @-mentioned agent without mutating the conversation's default.
    """
    # NOTE: read acp_session_id before any commit expires the instance —
    # _clarify_directives needs it to detect first-turn vs follow-up.
    is_first_turn = convo.acp_session_id is None
    reply_agent_id = agent_id_override or convo.primary_agent_id

    attached = await _resolve_attached_files(db, attached_file_ids or [], conversation_id=str(convo.id))
    if existing_user_msg is None:
        user_content: dict = {"text": text}
        if attached:
            user_content["files"] = attached
        user_msg = Message(
            conversation_id=convo.id,
            owner_id=owner_id,
            role="user",
            content=user_content,
            status="complete",
        )
        db.add(user_msg)
    else:
        user_msg = existing_user_msg
    agent_msg = Message(
        conversation_id=convo.id,
        role="agent",
        agent_id=reply_agent_id,
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
        }
    )
    return user_msg, agent_msg


async def send_roundtable(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    agents: list[str],
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    system_prompt: str | None = None,
    mentions: list[str] | None = None,
    profile_dir: str | None = None,
    existing_user_msg: Message | None = None,
) -> tuple[Message, Message]:
    """Multi-agent turn: one roundtable message holding per-agent replies + a
    synthesized merge. The runner streams each reply in parallel, then merges.

    Pass existing_user_msg when the caller already persisted the user turn
    (group dispatch) to avoid a duplicate user row.
    """
    attached = await _resolve_attached_files(db, attached_file_ids or [], conversation_id=str(convo.id))
    if existing_user_msg is None:
        user_content: dict = {"text": text}
        if attached:
            user_content["files"] = attached
        user_msg = Message(
            conversation_id=convo.id, owner_id=owner_id, role="user", content=user_content, mentions=mentions or [], status="complete"
        )
    else:
        user_msg = existing_user_msg
    rt_msg = Message(
        conversation_id=convo.id,
        role="roundtable",
        agent_id=agents[0],
        content={
            "replies": [
                {"agent_id": a, "text": "", "status": "streaming"} for a in agents
            ],
            "merged": {"text": "", "status": "pending"},
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
    # disallowed here because nobody can answer a modal mid-roundtable.
    prompt_text = f"{_FILE_WRITE_PREAMBLE}{_NO_CLARIFY_ROUNDTABLE}\n\n{prompt_text}"

    await redis_core.clear_cancel(str(convo.id))
    await redis_core.enqueue_prompt(
        {
            "type": "roundtable",
            "conversation_id": str(convo.id),
            "message_id": str(rt_msg.id),
            "agents": agents,
            "text": prompt_text,
            "system_prompt": system_prompt,
            "profile_dir": profile_dir,
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


async def dispatch(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    skip_agent: bool = False,
    profile_id_override: str | None = None,
) -> tuple[Message, Message | None]:
    """Route to single or roundtable based on the conversation's active agents."""
    agents = list(convo.active_agent_ids or [convo.primary_agent_id])
    if skip_agent:
        return await send_user_only(db, convo, text, attached_file_ids=attached_file_ids, owner_id=owner_id)

    # Load profile system_prompt — request-level override wins over conversation default
    system_prompt: str | None = None
    profile_dir: str | None = None
    effective_profile_id = profile_id_override or convo.profile_id
    if effective_profile_id:
        profile = await db.get(Profile, effective_profile_id)
        if profile:
            system_prompt = profile.system_prompt or None
            profile_dir = _profile_dir(profile)

    # Inject the user's long-term agent memory into system_prompt
    memory_prompt = await _build_memory_prompt(db, owner_id)
    if memory_prompt:
        system_prompt = f"{system_prompt}\n\n{memory_prompt}" if system_prompt else memory_prompt

    # Anti-clarify guidance only on follow-up turns — the first turn carries the
    # clarify preamble, and sending both contradicted each other.
    if convo.acp_session_id and len(agents) == 1:
        system_prompt = f"{system_prompt}\n\n{_ANTI_CLARIFY}" if system_prompt else _ANTI_CLARIFY

    if len(agents) > 1:
        return await send_roundtable(
            db, convo, text, agents,
            attached_file_ids=attached_file_ids, owner_id=owner_id,
            system_prompt=system_prompt, profile_dir=profile_dir,
        )
    return await send_message(
        db, convo, text,
        attached_file_ids=attached_file_ids, owner_id=owner_id,
        system_prompt=system_prompt, profile_dir=profile_dir,
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
            # Auto-add team shared agents (resolve from shared_profile_ids).
            if not member_agent_ids:
                member_agent_ids = await _resolve_team_agents(db, team)

    agent_ids = member_agent_ids or ["hermes"]
    convo = Conversation(
        title=title,
        owner_id=owner_id,
        type="group",
        primary_agent_id=agent_ids[0],
        active_agent_ids=agent_ids,
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

    # Add agent members
    for aid in agent_ids:
        db.add(GroupMember(
            conversation_id=convo.id,
            agent_id=aid,
            role="member",
        ))

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
    """@提及解析结果：Agent 桶 + 人类桶 + 全体标记。"""
    agent_ids: list[str] = field(default_factory=list)
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
    - "__all_agents__" → 群内全部 Agent
    - "__all_humans__" → all_humans 标记（仅通知，不触发 AI）
    - "user:{uuid}"    → 人类成员（校验确为成员）
    - 裸 agent id       → Agent（校验确在群内）
    """
    out = ResolvedMentions()
    if not mentions:
        return out

    members = await get_group_members(db, conversation_id)
    group_agents = [m.agent_id for m in members if m.agent_id]
    group_user_ids = {str(m.user_id) for m in members if m.user_id}

    if "__all_agents__" in mentions:
        out.all_agents = True
        out.agent_ids = list(dict.fromkeys(group_agents))
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
        # Bare agent id — trust it only if it is actually a group agent
        if mention in group_agents and mention not in out.agent_ids:
            out.agent_ids.append(mention)

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
        "content": msg.content or {},
        "status": msg.status,
        "mentions": msg.mentions or [],
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "reply_to_id": str(msg.reply_to_id) if msg.reply_to_id else None,
        "reply_to": await _build_reply_ref(db, msg.reply_to_id),
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
) -> tuple[Message, Message | None]:
    """群聊消息路由：按 channel_mode + mentions 决定走人→人 / 人→机 / 圆桌。

    无论是否触发 AI，人类消息总是先持久化并实时广播给全体成员（含发送者回显）。
    """
    resolved = await resolve_mentions(db, convo.id, mentions)
    mode = getattr(convo, "channel_mode", "mention") or "mention"

    # 计算 Agent 目标
    agent_targets = list(resolved.agent_ids)
    if mode == "always" and not agent_targets and not resolved.all_humans and not skip_agent:
        members = await get_group_members(db, convo.id)
        agent_targets = [m.agent_id for m in members if m.agent_id]

    save_only = (
        mode == "off" or skip_agent or resolved.all_humans or not agent_targets
    )

    # 始终先落库 + 广播人类消息
    user_msg = await _persist_group_user_msg(
        db, convo, text, mentions, owner_id,
        attached_file_ids=attached_file_ids, reply_to_id=reply_to_id,
    )
    await _publish_user_message(db, convo, user_msg, resolved)

    if save_only:
        return user_msg, None

    if len(agent_targets) == 1:
        # 人→机模式：单 Agent
        system_prompt = None
        profile_dir = None
        # 优先级：显式 profile_id_override > 被@Agent 的 profile > 会话默认
        effective_profile_id = profile_id_override or convo.profile_id
        if not profile_id_override:
            res_p = await db.execute(
                select(Profile).where(
                    Profile.default_agent_id == agent_targets[0],
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

        memory_prompt = await _build_memory_prompt(db, owner_id)
        if memory_prompt:
            system_prompt = f"{system_prompt}\n\n{memory_prompt}" if system_prompt else memory_prompt
        if convo.acp_session_id:
            system_prompt = f"{system_prompt}\n\n{_ANTI_CLARIFY}" if system_prompt else _ANTI_CLARIFY

        # Attribute the reply to the @-mentioned agent without mutating the
        # conversation's stable primary_agent_id default.
        _, agent_msg = await send_message(
            db, convo, text,
            attached_file_ids=attached_file_ids,
            owner_id=owner_id,
            system_prompt=system_prompt,
            existing_user_msg=user_msg,
            profile_dir=profile_dir,
            agent_id_override=agent_targets[0],
        )
        return user_msg, agent_msg

    # 圆桌模式：多 Agent 并行
    system_prompt = None
    profile_dir = None
    effective_profile_id = profile_id_override or convo.profile_id
    if effective_profile_id:
        profile = await db.get(Profile, effective_profile_id)
        if profile:
            system_prompt = profile.system_prompt or None
            profile_dir = _profile_dir(profile)

    memory_prompt = await _build_memory_prompt(db, owner_id)
    if memory_prompt:
        system_prompt = f"{system_prompt}\n\n{memory_prompt}" if system_prompt else memory_prompt

    return await send_roundtable(
        db, convo, text, agent_targets,
        attached_file_ids=attached_file_ids,
        owner_id=owner_id,
        system_prompt=system_prompt,
        mentions=mentions,
        profile_dir=profile_dir,
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


async def _resolve_team_agents(db: AsyncSession, team) -> list[str]:
    """Resolve shared Profile ids → agent ids for team dispatch (batch query).

    Only shared_profile_ids is consulted (shared_agents column was dropped).
    Falls back to ["hermes"] when no profiles are shared or none resolve.
    """
    pids_raw = [pid for pid in (team.shared_profile_ids or []) if pid]
    valid_uuids = []
    for pid in pids_raw:
        try:
            valid_uuids.append(uuid.UUID(pid))
        except (ValueError, TypeError):
            continue
    if not valid_uuids:
        return ["hermes"]
    result = await db.execute(select(Profile).where(Profile.id.in_(valid_uuids)))
    agent_ids: list[str] = []
    for p in result.scalars().all():
        if p.default_agent_id and p.default_agent_id not in agent_ids:
            agent_ids.append(p.default_agent_id)
    return agent_ids or ["hermes"]


async def sync_group_membership(
    db: AsyncSession,
    convo: Conversation,
    *,
    human_user_ids: list[uuid.UUID],
    agent_ids: list[str],
) -> None:
    """把群成员与团队/项目成员集做差量增删；admin 成员保留。"""
    members = await get_group_members(db, convo.id)
    existing_users = {m.user_id for m in members if m.user_id}
    existing_agents = {m.agent_id for m in members if m.agent_id}
    desired_users = set(human_user_ids)
    desired_agents = set(agent_ids)
    changed = False

    for uid in desired_users - existing_users:
        db.add(GroupMember(conversation_id=convo.id, user_id=uid, role="member"))
        changed = True
    for aid in desired_agents - existing_agents:
        db.add(GroupMember(conversation_id=convo.id, agent_id=aid, role="member"))
        changed = True
    for m in members:
        if m.user_id and m.user_id not in desired_users and m.role != "admin":
            await db.delete(m)
            changed = True
        elif m.agent_id and m.agent_id not in desired_agents:
            await db.delete(m)
            changed = True

    if changed:
        if desired_agents:
            convo.active_agent_ids = list(desired_agents)
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
    agents = await _resolve_team_agents(db, team)

    if convo is None:
        convo = Conversation(
            title=f"{team.name} · 群聊",
            owner_id=owner_id,
            type="group",
            primary_agent_id=agents[0],
            active_agent_ids=agents,
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

    await sync_group_membership(db, convo, human_user_ids=human_ids, agent_ids=agents)
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
    # Resolve pinned Profile ids → agent ids for project dispatch.
    agents: list[str] = []
    for pid in (project.pinned_profile_ids or []):
        try:
            p = await db.get(Profile, uuid.UUID(pid))
        except (ValueError, TypeError):
            continue
        if p and p.default_agent_id and p.default_agent_id not in agents:
            agents.append(p.default_agent_id)
    agents = agents or ["hermes"]

    if convo is None:
        convo = Conversation(
            title=f"{project.name} · 群聊",
            owner_id=owner_id,
            type="group",
            primary_agent_id=agents[0],
            active_agent_ids=agents,
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

    await sync_group_membership(db, convo, human_user_ids=human_ids, agent_ids=agents)
    return convo
