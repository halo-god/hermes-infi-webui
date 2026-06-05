"""Conversation + message persistence and the send→enqueue hot path."""
from __future__ import annotations

import os
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import redis as redis_core
from app.db.models.conversation import Conversation, Message
from app.db.models.workspace import WorkspaceFile, WorkspaceFileVersion


async def list_conversations(
    db: AsyncSession,
    owner_id: uuid.UUID,
    *,
    q: str | None = None,
    pinned_only: bool = False,
) -> list[Conversation]:
    stmt = select(Conversation).where(Conversation.owner_id == owner_id)
    if pinned_only:
        stmt = stmt.where(Conversation.pinned.is_(True))
    if q:
        escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(func.lower(Conversation.title).like(f"%{escaped}%", escape="\\"))
    stmt = stmt.order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def bulk_delete(
    db: AsyncSession, owner_id: uuid.UUID, ids: list[uuid.UUID]
) -> int:
    res = await db.execute(
        select(Conversation).where(
            Conversation.owner_id == owner_id, Conversation.id.in_(ids)
        )
    )
    convos = list(res.scalars().all())
    for c in convos:
        await db.delete(c)
    await db.commit()
    return len(convos)


async def get_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, owner_id: uuid.UUID
) -> Conversation | None:
    res = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            (Conversation.owner_id == owner_id) | Conversation.is_channel.is_(True),
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

            # Write file content to workspace so agent can read it
            if ws_dir and file_content and not is_image:
                fpath = os.path.join(ws_dir, f.name)
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(file_content)

            result.append({
                "id": str(f.id), "name": f.name, "kind": f.kind,
                "workspace_path": f"attachments/{f.name}" if ws_dir and file_content else None,
                "content": file_content,
                "size_bytes": f.size_bytes or len(file_content),
                "mime_type": mime,
                "is_image": is_image,
                "is_text": is_text,
            })
    return result


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


async def send_message(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
) -> tuple[Message, Message]:
    """Persist the user turn + an empty streaming agent turn, then enqueue ACP work.

    The per-token hot path does NOT touch the DB — the runner streams via Redis
    Pub/Sub and writes the agent message once on completion.
    """
    attached = await _resolve_attached_files(db, attached_file_ids or [], conversation_id=str(convo.id))
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
    agent_msg = Message(
        conversation_id=convo.id,
        role="agent",
        agent_id=convo.primary_agent_id,
        content={"text": ""},
        status="streaming",
    )
    db.add_all([user_msg, agent_msg])

    # Auto-title from the first user message.
    if convo.title == "新会话":
        convo.title = text[:40]

    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(agent_msg)

    # Build prompt — use ACP content blocks for structured attachment handling.
    # Images: ImageContentBlock (base64), Text files: Resource Link + inline fallback.
    _file_write_preamble = (
        "【文件写入规范】当你需要为用户创建、生成或导出文件时，"
        "必须使用 write_file 工具将文件写入当前工作目录（cwd）。"
        "文件路径使用相对路径（如 'README.md'、'src/main.py'），不要使用绝对路径。"
        "不要只在回复文本中说\"文件已生成\"或给出文件路径而不实际写入。"
        "文件名请使用有意义的名称（如 会议纪要.md、report.csv），不要使用临时路径。"
    )
    _clarification_preamble = (
        "\n\n【强制规则：必须先确认再行动】\n"
        "当用户的请求有以下任一情况时，你必须先调用 clarify 工具，不要直接回答：\n"
        "- 请求模糊、有多种理解方式\n"
        "- 需要用户选择方向、风格、范围\n"
        "- 涉及重要决策或有风险的操作\n"
        "- 用户输入非常简短（少于10个字）\n\n"
        "调用方式（必须是工具调用，不要输出文本格式）：\n"
        'clarify(question="问题", choices=["选项A", "选项B", "选项C"])\n'
        'clarify(question="你具体想要什么？")  # 无选项时用 open-ended\n\n'
        "禁止在回复文本中输出 [确认] 或类似的标记格式。必须通过工具调用 clarify。\n"
        "违反此规则会导致用户不满。记住：先问再做。"
    )
    _strict_mode = ""
    if len(text.strip()) < 15:
        _strict_mode = (
            "\n\n【严格模式】用户输入非常简短，你无法确定用户意图。"
            "你必须先调用 clarify 工具询问用户想要什么，不要直接猜测并执行。"
            "这是强制要求，不是建议。"
        )

    # Build content blocks for ACP protocol
    prompt_blocks: list[dict] = []

    # Text block: preambles + user message + inline file references
    prompt_text = text
    if attached:
        text_parts = []
        for f in attached:
            if f.get("is_image"):
                # Images go as separate ImageContentBlock, just reference in text
                text_parts.append(f"[图片附件: {f['name']}]")
            else:
                # Text files: include inline as before (fallback for agents that don't support resource_link)
                content = f.get("content", "")
                if content:
                    if len(content) > 200000:
                        content = content[:200000] + f"\n\n... [文件截断，共 {len(f.get('content', ''))} 字符]"
                    text_parts.append(f"【附件: {f['name']}】\n```\n{content}\n```")
                else:
                    text_parts.append(f"【附件: {f['name']}】（文件内容为空）")
        if text_parts:
            prompt_text = f"{text}\n\n" + "\n\n".join(text_parts)

    full_text = f"{_file_write_preamble}{_clarification_preamble}{_strict_mode}\n\n{prompt_text}"
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
            "agent_id": convo.primary_agent_id,
            "text": full_text,
            "content_blocks": prompt_blocks if len(prompt_blocks) > 1 else None,
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
) -> tuple[Message, Message]:
    """Multi-agent turn: one roundtable message holding per-agent replies + a
    synthesized merge. The runner streams each reply in parallel, then merges."""
    attached = await _resolve_attached_files(db, attached_file_ids or [], conversation_id=str(convo.id))
    user_content: dict = {"text": text}
    if attached:
        user_content["files"] = attached
    user_msg = Message(
        conversation_id=convo.id, owner_id=owner_id, role="user", content=user_content, status="complete"
    )
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
    db.add_all([user_msg, rt_msg])
    if convo.title == "新会话":
        convo.title = text[:40]
    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(rt_msg)

    prompt_text = text
    if attached:
        parts = []
        for f in attached:
            content = f.get("content", "")
            if content:
                if len(content) > 200000:
                    content = content[:200000] + f"\n\n... [文件截断，共 {len(f.get('content', ''))} 字符]"
                parts.append(f"【附件: {f['name']}】\n```\n{content}\n```")
            else:
                parts.append(f"【附件: {f['name']}】（文件内容为空）")
        file_block = "\n\n".join(parts)
        prompt_text = f"{text}\n\n{file_block}"

    # Inject file-write instructions for roundtable agents too
    _file_write_preamble = (
        "【文件写入规范】当你需要为用户创建、生成或导出文件时，"
        "必须使用 write_file 工具将文件写入当前工作目录（cwd）。"
        "文件路径使用相对路径（如 'README.md'、'src/main.py'），不要使用绝对路径。"
        "不要只在回复文本中说\"文件已生成\"或给出文件路径而不实际写入。"
        "文件名请使用有意义的名称（如 会议纪要.md、report.csv），不要使用临时路径。"
    )
    _clarification_preamble = (
        "\n\n【强制规则：必须先确认再行动】\n"
        "当用户的请求有以下任一情况时，你必须先调用 clarify 工具，不要直接回答：\n"
        "- 请求模糊、有多种理解方式\n"
        "- 需要用户选择方向、风格、范围\n"
        "- 涉及重要决策或有风险的操作\n"
        "- 用户输入非常简短（少于10个字）\n\n"
        "调用方式（必须是工具调用，不要输出文本格式）：\n"
        'clarify(question="问题", choices=["选项A", "选项B", "选项C"])\n'
        'clarify(question="你具体想要什么？")  # 无选项时用 open-ended\n\n'
        "禁止在回复文本中输出 [确认] 或类似的标记格式。必须通过工具调用 clarify。\n"
        "违反此规则会导致用户不满。记住：先问再做。"
    )
    # Strict mode for short prompts: force clarify call
    _strict_mode = ""
    if len(text.strip()) < 15:
        _strict_mode = (
            "\n\n【严格模式】用户输入非常简短，你无法确定用户意图。"
            "你必须先调用 clarify 工具询问用户想要什么，不要直接猜测并执行。"
            "这是强制要求，不是建议。"
        )
    prompt_text = f"{_file_write_preamble}{_clarification_preamble}{_strict_mode}\n\n{prompt_text}"

    await redis_core.clear_cancel(str(convo.id))
    await redis_core.enqueue_prompt(
        {
            "type": "roundtable",
            "conversation_id": str(convo.id),
            "message_id": str(rt_msg.id),
            "agents": agents,
            "text": prompt_text,
        }
    )
    return user_msg, rt_msg


async def dispatch(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    attached_file_ids: list[str] | None = None,
    owner_id: uuid.UUID | None = None,
    skip_agent: bool = False,
) -> tuple[Message, Message | None]:
    """Route to single or roundtable based on the conversation's active agents."""
    agents = list(convo.active_agent_ids or [convo.primary_agent_id])
    if skip_agent:
        return await send_user_only(db, convo, text, attached_file_ids=attached_file_ids, owner_id=owner_id)
    if len(agents) > 1:
        return await send_roundtable(db, convo, text, agents, attached_file_ids=attached_file_ids, owner_id=owner_id)
    return await send_message(db, convo, text, attached_file_ids=attached_file_ids, owner_id=owner_id)


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
    all_msgs = await get_messages(db, source_id)
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
    ver = WorkspaceFileVersion(
        file_id=f.id,
        version_num=f.current_version,
        content=f.content,
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
