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
        stmt = stmt.where(func.lower(Conversation.title).like(f"%{q.lower()}%"))
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


async def get_messages(db: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    res = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return list(res.scalars().all())


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
    """Look up workspace files by id, write to agent workspace dir, return metadata.

    Returns [{id, name, kind, workspace_path}] — no content inline.
    Agent reads files on demand via read_file tool from its workspace CWD.
    """
    if not file_ids:
        return []

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
            # Write file content to workspace so agent can read it
            if ws_dir and f.content:
                fpath = os.path.join(ws_dir, f.name)
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(f.content)
                result.append({
                    "id": str(f.id), "name": f.name, "kind": f.kind,
                    "workspace_path": f"attachments/{f.name}",
                })
            else:
                result.append({
                    "id": str(f.id), "name": f.name, "kind": f.kind,
                    "workspace_path": None,
                })
    return result


async def send_message(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    attached_file_ids: list[str] | None = None,
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

    # Build prompt — file references only, agent reads via read_file tool.
    prompt_text = text
    if attached:
        parts = []
        for f in attached:
            wp = f.get("workspace_path")
            if wp:
                parts.append(f"[附件: {f['name']}] (文件路径: {wp}，请用 read_file 读取内容)")
            else:
                parts.append(f"[附件: {f['name']}]（文件内容为空）")
        file_block = "\n\n".join(parts)
        prompt_text = f"{text}\n\n{file_block}"

    await redis_core.clear_cancel(str(convo.id))
    await redis_core.enqueue_prompt(
        {
            "type": "single",
            "conversation_id": str(convo.id),
            "message_id": str(agent_msg.id),
            "agent_id": convo.primary_agent_id,
            "text": prompt_text,
        }
    )
    return user_msg, agent_msg


async def send_roundtable(
    db: AsyncSession,
    convo: Conversation,
    text: str,
    agents: list[str],
    attached_file_ids: list[str] | None = None,
) -> tuple[Message, Message]:
    """Multi-agent turn: one roundtable message holding per-agent replies + a
    synthesized merge. The runner streams each reply in parallel, then merges."""
    attached = await _resolve_attached_files(db, attached_file_ids or [], conversation_id=str(convo.id))
    user_content: dict = {"text": text}
    if attached:
        user_content["files"] = attached
    user_msg = Message(
        conversation_id=convo.id, role="user", content=user_content, status="complete"
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
            wp = f.get("workspace_path")
            if wp:
                parts.append(f"[附件: {f['name']}] (文件路径: {wp}，请用 read_file 读取内容)")
            else:
                parts.append(f"[附件: {f['name']}]（文件内容为空）")
        file_block = "\n\n".join(parts)
        prompt_text = f"{text}\n\n{file_block}"

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
) -> tuple[Message, Message]:
    """Route to single or roundtable based on the conversation's active agents."""
    agents = list(convo.active_agent_ids or [convo.primary_agent_id])
    if len(agents) > 1:
        return await send_roundtable(db, convo, text, agents, attached_file_ids=attached_file_ids)
    return await send_message(db, convo, text, attached_file_ids=attached_file_ids)


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
