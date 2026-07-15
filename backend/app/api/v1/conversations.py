"""Conversations: CRUD, send message, SSE stream, cancel, workspace files."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from fastapi import (
    APIRouter,
    Depends,
    File as FastApiFile,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import metrics, ratelimit
from app.core import redis as redis_core
from app.core.files import (
    read_upload_capped,
    process_upload,
    is_text_extractable,
    OFFICE_EXTRACTORS,
)
from app.core import object_storage
from app.db.base import async_session_maker, get_db
from app.db.models.user import User
from app.db.models.workspace import WorkspaceFile
from app.deps import get_current_user, user_from_media_ticket, user_from_ticket_or_header

from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationFolderCreate,
    ConversationFolderOut,
    ConversationFolderReorder,
    ConversationFolderUpdate,
    ConversationOut,
    ConversationUpdate,
    ConfirmRequest,
    EditMessageRequest,
    GroupCreate,
    AddMemberRequest,
    GroupMemberOut,
    MemberUpdateRequest,
    MarkReadResponse,
    MessageOut,
    ReactionRequest,
    SendMessageRequest,
    SendMessageResponse,
    SetAgentsRequest,
    SetSessionModeRequest,
    SetSessionModelRequest,
    WorkspaceFileDetail,
    WorkspaceFileOut,
    WorkspaceFileVersionOut,
)
from app.db.models.conversation import ConversationFolder
from app.schemas.subagent import SubagentOut, SubagentSend, SubagentSpawn
from app.schemas.team import ConsolidateRequest
from app.services import conversation_service as svc
from app.services import subagent_service

router = APIRouter()


async def _enrich_messages_with_files(
    db: AsyncSession, msgs: list, conversation_id: uuid.UUID
) -> list[MessageOut]:
    """Attach workspace files to message content.files for persisted messages."""
    from sqlalchemy import select

    msg_ids = [m.id for m in msgs]
    if not msg_ids:
        return [MessageOut.model_validate(m) for m in msgs]

    # Fetch files linked to these messages
    res = await db.execute(
        select(WorkspaceFile).where(
            WorkspaceFile.conversation_id == conversation_id,
            WorkspaceFile.message_id.isnot(None),
            WorkspaceFile.message_id.in_(msg_ids),
        )
    )
    files_by_msg: dict[uuid.UUID, list[dict]] = {}
    for f in res.scalars().all():
        files_by_msg.setdefault(f.message_id, []).append(
            {"id": str(f.id), "name": f.name, "kind": f.kind}
        )

    # Batch-load referenced (reply-to) messages for the quote preview.
    from app.db.models.conversation import Message as MessageModel
    from app.schemas.conversation import ReplyRef

    ref_ids = {m.reply_to_id for m in msgs if m.reply_to_id}
    refs_by_id: dict[uuid.UUID, ReplyRef] = {}
    if ref_ids:
        rres = await db.execute(
            select(MessageModel).where(MessageModel.id.in_(ref_ids))
        )
        for r in rres.scalars().all():
            snippet = (r.content or {}).get("text") or ""
            refs_by_id[r.id] = ReplyRef(
                id=r.id, role=r.role, owner_id=r.owner_id,
                agent_id=r.agent_id, snippet=snippet[:80],
            )

    result = []
    for m in msgs:
        out = MessageOut.model_validate(m)
        file_list = files_by_msg.get(m.id)
        if file_list:
            out.content = {**out.content, "files": file_list}
        if m.reply_to_id:
            out.reply_to = refs_by_id.get(m.reply_to_id)
        result.append(out)
    return result


async def _require_convo(db, conversation_id: uuid.UUID, user: User):
    convo = await svc.get_conversation(db, conversation_id, user.id)
    if convo is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return convo




def _truncate_knowledge_blocks(text: str, max_block: int = 100_000) -> str:
    """Truncate oversized <knowledge> blocks to prevent context length explosion."""
    def _repl(m):
        block = m.group(0)
        if len(block) > max_block:
            return block[:max_block] + "\n\n... [内容已截断，文件较大，请使用 read_file 工具分段读取]\n</knowledge>"
        return block
    return re.sub(r"<knowledge>.*?</knowledge>", _repl, text, flags=re.DOTALL)

@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    q: str | None = Query(None),
    pinned: bool = Query(False),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await svc.list_conversations(
        db, user.id, q=q, pinned_only=pinned, limit=limit, offset=offset
    )


# ── conversation folders (grouping) ──────────────────────────────────
@router.get("/folders", response_model=list[ConversationFolderOut])
async def list_folders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(ConversationFolder)
            .where(ConversationFolder.owner_id == user.id)
            .order_by(
                ConversationFolder.pinned.desc(),
                ConversationFolder.sort_order,
                ConversationFolder.created_at,
            )
        )
    ).scalars().all()
    return rows


@router.post("/folders", response_model=ConversationFolderOut, status_code=201)
async def create_folder(
    payload: ConversationFolderCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Next sort_order = current max + 1
    max_order = (
        await db.execute(
            select(func.max(ConversationFolder.sort_order)).where(
                ConversationFolder.owner_id == user.id
            )
        )
    ).scalar_one_or_none() or 0

    folder = ConversationFolder(
        owner_id=user.id,
        name=payload.name.strip(),
        sort_order=max_order + 1,
    )
    db.add(folder)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名文件夹已存在")
    await db.refresh(folder)
    return folder


@router.patch("/folders/{folder_id}", response_model=ConversationFolderOut)
async def update_folder(
    folder_id: uuid.UUID,
    payload: ConversationFolderUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    folder = (
        await db.execute(
            select(ConversationFolder).where(
                ConversationFolder.id == folder_id,
                ConversationFolder.owner_id == user.id,
            )
        )
    ).scalars().first()
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在")

    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "name" and value is not None:
            value = value.strip()
        setattr(folder, field, value)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同名文件夹已存在")
    await db.refresh(folder)
    return folder


@router.put("/folders/reorder")
async def reorder_folders(
    payload: ConversationFolderReorder,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Batch-update sort_order for multiple folders (drag-to-reorder)."""
    ids = [item["id"] for item in payload.items if item.get("id")]
    if not ids:
        return {"status": "ok"}
    # Batch-load all folders in one query.
    rows = {
        f.id: f
        for f in (
            await db.execute(
                select(ConversationFolder).where(
                    ConversationFolder.id.in_(ids),
                    ConversationFolder.owner_id == user.id,
                )
            )
        ).scalars().all()
    }
    for item in payload.items:
        f = rows.get(uuid.UUID(item["id"]))
        if f:
            f.sort_order = item["sort_order"]
    await db.commit()
    return {"status": "ok"}


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    folder = (
        await db.execute(
            select(ConversationFolder).where(
                ConversationFolder.id == folder_id,
                ConversationFolder.owner_id == user.id,
            )
        )
    ).scalars().first()
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    # ON DELETE SET NULL on conversations.folder_id clears the reference.
    await db.delete(folder)
    await db.commit()


@router.post("/bulk-delete")
async def bulk_delete(
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw = payload.get("ids") or []
    try:
        ids = [uuid.UUID(str(x)) for x in raw]
    except ValueError:
        raise HTTPException(status_code=422, detail="无效的会话 id")
    deleted = await svc.bulk_delete(db, user.id, ids)
    return {"deleted": deleted}


@router.post("", response_model=ConversationDetail, status_code=201)
async def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await svc.create_conversation(
        db,
        user.id,
        title=payload.title,
        primary_agent_id=payload.primary_agent_id,
        profile_id=payload.profile_id,
        team_id=payload.team_id,
        project_id=payload.project_id,
    )
    if payload.first_message:
        await svc.send_message(db, convo, payload.first_message, owner_id=user.id)
    msgs = await svc.get_messages(db, convo.id)
    return ConversationDetail(
        **ConversationOut.model_validate(convo).model_dump(),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


@router.get("/groups", response_model=list)
async def list_groups(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出用户参与的所有群聊（附带未读数与@提醒标记）。"""
    convos = await svc.list_group_conversations(db, user.id)
    summary = await svc.unread_summary(db, user.id, [c.id for c in convos])
    out = []
    for c in convos:
        d = ConversationOut.model_validate(c).model_dump()
        s = summary.get(str(c.id), {})
        d["unread"] = s.get("unread", 0)
        d["has_mention"] = s.get("mention", False)
        out.append(d)
    return out


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    # Initial load: last 50 messages
    msgs = await svc.get_messages(db, convo.id, limit=50)
    enriched = await _enrich_messages_with_files(db, msgs, convo.id)
    return ConversationDetail(
        **ConversationOut.model_validate(convo).model_dump(),
        messages=enriched,
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages_page(
    conversation_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    before: uuid.UUID | None = Query(None, description="Cursor: message ID to fetch before"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Paginated message fetch. Use *before* cursor for infinite scroll (load older)."""
    await _require_convo(db, conversation_id, user)
    msgs = await svc.get_messages(db, conversation_id, limit=limit, before_id=before)
    return await _enrich_messages_with_files(db, msgs, conversation_id)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(convo, field, value)
    await db.commit()
    await db.refresh(convo)
    return convo


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    await svc.delete_conversation(db, convo)


@router.post("/{conversation_id}/fork", response_model=ConversationDetail, status_code=201)
async def fork_conversation(
    conversation_id: uuid.UUID,
    before_message_id: uuid.UUID = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        fork, msgs = await svc.fork_conversation(db, conversation_id, user.id, before_message_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ConversationDetail(
        **ConversationOut.model_validate(fork).model_dump(),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


@router.post("/{conversation_id}/share")
async def share_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    convo.visibility = "shared"
    await db.commit()
    await db.refresh(convo)
    return {"share_url": f"/shared/{convo.id}", "conversation_id": str(convo.id)}


@router.get("/shared/{conversation_id}", response_model=ConversationDetail)
async def get_shared_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.db.models.conversation import Conversation
    from app.core import ratelimit

    # Rate-limit per IP to prevent UUID enumeration.
    ip = request.client.host if request.client else "unknown"
    try:
        allowed, _ = await ratelimit.hit(f"rl:shared:{ip}", 30, 60)
    except Exception:
        allowed = False  # fail-closed
    if not allowed:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    res = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.visibility == "shared",
        )
    )
    convo = res.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="分享链接不存在或已失效")
    # Cap at 50 messages for unauthenticated viewers.
    msgs = await svc.get_messages(db, convo.id, limit=50)
    return ConversationDetail(
        **ConversationOut.model_validate(convo).model_dump(),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


@router.post("/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    if not await ratelimit.allow_send(str(user.id)):
        raise HTTPException(status_code=429, detail="发送过于频繁，请稍后再试")
    metrics.MESSAGES.inc()

    # Group chat: route via @mentions
    if convo.type == "group":
        # Truncate oversized <knowledge> blocks injected by the frontend
        # to prevent context length explosion (max 100KB per knowledge block).
        text = _truncate_knowledge_blocks(payload.text)
        user_msg, agent_msg = await svc.dispatch_group(
            db, convo, text, payload.mentions,
            attached_file_ids=payload.attached_file_ids,
            knowledge_ids=payload.knowledge_ids,
            owner_id=user.id,
            skip_agent=payload.skip_agent,
            reply_to_id=payload.reply_to_id,
            task_id=payload.task_id,
        )
    else:
        text = _truncate_knowledge_blocks(payload.text)
        user_msg, agent_msg = await svc.dispatch(
            db, convo, text,
            attached_file_ids=payload.attached_file_ids,
            knowledge_ids=payload.knowledge_ids,
            owner_id=user.id,
            skip_agent=payload.skip_agent,
            profile_id_override=payload.profile_id,
            task_id=payload.task_id,
        )

    return SendMessageResponse(
        user_message=MessageOut.model_validate(user_msg),
        agent_message=MessageOut.model_validate(agent_msg) if agent_msg else None,
    )


@router.put("/{conversation_id}/agents", response_model=ConversationOut)
async def set_agents(
    conversation_id: uuid.UUID,
    payload: SetAgentsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    return await svc.set_active_agents(db, convo, payload.agent_ids)


@router.post("/{conversation_id}/cancel", status_code=202)
async def cancel(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    await redis_core.request_cancel(str(conversation_id))
    return {"status": "cancelling"}


_STREAM_ID_RE = re.compile(r"^\d+(-\d+)?$")


@router.get("/{conversation_id}/stream")
async def stream(
    conversation_id: uuid.UUID,
    request: Request,
    ticket: str = Query(..., description="media ticket (EventSource cannot set headers)"),
    since: str | None = Query(None, description="resume after this stream id (e.g. '1700000000000-0')"),
    db: AsyncSession = Depends(get_db),
):
    """SSE live stream of agent events for a conversation.

    Events live in a capped per-conversation Redis Stream, so unlike Pub/Sub
    there is no subscribe-after-publish loss, and reconnects replay: each SSE
    frame carries the stream entry as its `id`, EventSource resends it as the
    Last-Event-ID header on auto-reconnect (the `since` param covers manual
    resume). No DB on the per-event path. Auth is a short-lived media ticket,
    not the API access token — so a leaked stream URL can't call the API.
    """
    user = await user_from_media_ticket(ticket, db)
    convo = await svc.get_conversation(db, conversation_id, user.id)
    if convo is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    cid = str(conversation_id)
    resume_id = request.headers.get("last-event-id") or since
    if resume_id and not _STREAM_ID_RE.match(resume_id):
        resume_id = None

    async def event_gen():
        # Capture the position BEFORE the prelude: anything published after this
        # point is delivered, anything before is the caller's chosen resume point.
        last_id = resume_id or await redis_core.latest_event_id(cid)
        yield ": connected\n\n"  # prelude opens the stream promptly
        while True:
            if await request.is_disconnected():
                break
            # Short block so we check disconnection frequently (cancel needs
            # fast feedback; 8s default was too slow).
            entries = await redis_core.read_events(cid, last_id, block_ms=2000)
            if not entries:
                yield ": keepalive\n\n"  # heartbeat
                continue
            for entry_id, data in entries:
                last_id = entry_id
                yield f"id: {entry_id}\ndata: {data}\n\n"
            # NOTE: the stream never closes on "done" — the frontend owns the
            # lifecycle (clarify-resume emits done → start on the same stream).

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx proxy buffering
        },
    )


@router.websocket("/{conversation_id}/ws")
async def conversation_ws(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    ticket: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Bidirectional channel: client sends {action:'send'|'cancel', text?},
    server relays all conversation events (single + roundtable). Used by the
    roundtable UI; one persistent socket per open conversation. Auth is a
    short-lived media ticket, not the API access token."""
    try:
        user = await user_from_media_ticket(ticket, db)
    except HTTPException:
        await websocket.close(code=4401)
        return
    # Capture the id now: the request-scoped `db` session is long gone by the
    # time later frames arrive, and touching the detached ORM user then would
    # raise MissingGreenlet/DetachedInstanceError.
    user_id = user.id
    convo = await svc.get_conversation(db, conversation_id, user_id)
    if convo is None:
        await websocket.close(code=4404)
        return

    cid = str(conversation_id)
    # Capture the stream position before accept(): nothing the client triggers
    # after the handshake can be published before this point — zero loss.
    last_id = await redis_core.latest_event_id(cid)
    await websocket.accept()

    async def pump_out():
        nonlocal last_id
        while True:
            entries = await redis_core.read_events(cid, last_id, block_ms=2000)
            for entry_id, data in entries:
                last_id = entry_id
                await websocket.send_text(data)

    out_task = asyncio.create_task(pump_out())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            action = payload.get("action")
            # Handle ping/pong for keepalive
            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue
            if action == "typing":
                # Ephemeral presence ping — broadcast to other members, not persisted.
                await redis_core.publish_event(cid, {
                    "type": "typing",
                    "user_id": str(user_id),
                    "name": payload.get("name") or "",
                })
                continue
            if action == "send":
                text = (payload.get("text") or "").strip()
                if text:
                    if not await ratelimit.allow_send(str(user_id)):
                        await websocket.send_text(
                            json.dumps({"type": "error", "message_id": "", "detail": "发送过于频繁"})
                        )
                        continue
                    file_ids = payload.get("attached_file_ids") or []
                    knowledge_ids = payload.get("knowledge_ids") or []
                    p_id = payload.get("profileId") or payload.get("profile_id") or None
                    mentions = payload.get("mentions") or []
                    reply_raw = payload.get("reply_to_id")
                    reply_to_id = None
                    if reply_raw:
                        try:
                            reply_to_id = uuid.UUID(str(reply_raw))
                        except (ValueError, TypeError):
                            reply_to_id = None
                    task_raw = payload.get("task_id")
                    task_id = None
                    if task_raw:
                        try:
                            task_id = uuid.UUID(str(task_raw))
                        except (ValueError, TypeError):
                            task_id = None
                    # Use a fresh DB session for each message to avoid detached instances.
                    # The initial `db` session is invalid after the first await in the loop.
                    async with async_session_maker() as msg_db:
                        c = await svc.get_conversation(msg_db, conversation_id, user_id)
                        if c and c.type == "group":
                            # Group: route via @mentions (human↔human / human↔AI / roundtable).
                            # profileId is a personal-chat concept (the Composer's default
                            # assistant) and must never override who a group @-mention
                            # resolved to — deliberately not forwarded here.
                            await svc.dispatch_group(
                                msg_db, c, text, mentions,
                                attached_file_ids=file_ids, knowledge_ids=knowledge_ids,
                                owner_id=user_id,
                                reply_to_id=reply_to_id,
                                task_id=task_id,
                            )
                        elif c:
                            await svc.dispatch(
                                msg_db, c, text, attached_file_ids=file_ids,
                                knowledge_ids=knowledge_ids, owner_id=user_id,
                                profile_id_override=p_id, task_id=task_id,
                            )
            elif action == "cancel":
                await redis_core.request_cancel(cid)
    except WebSocketDisconnect:
        pass
    finally:
        out_task.cancel()


@router.get("/{conversation_id}/files", response_model=list[WorkspaceFileOut])
async def list_files(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    return await svc.list_files(db, conversation_id)


@router.get("/{conversation_id}/files/{file_id}", response_model=WorkspaceFileDetail)
async def get_file(
    conversation_id: uuid.UUID,
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    f = await db.get(WorkspaceFile, file_id)
    if f is None or f.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    content = f.content
    if content is None and f.storage_key:
        data = await asyncio.to_thread(object_storage.get, f.storage_key)
        if f.kind in OFFICE_EXTRACTORS:
            # Re-extract HTML from the stored original bytes.
            content = OFFICE_EXTRACTORS[f.kind](data) or None
        elif is_text_extractable(f.kind):
            content = data.decode("utf-8", "ignore")
        else:
            content = None
    return WorkspaceFileDetail(
        **WorkspaceFileOut.model_validate(f).model_dump(), content=content
    )


@router.get("/{conversation_id}/files/{file_id}/raw")
async def get_file_raw(
    conversation_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    ticket: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import Response

    user = await user_from_ticket_or_header(ticket, request, db)
    await _require_convo(db, conversation_id, user)
    f = await db.get(WorkspaceFile, file_id)
    if f is None or f.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="文件不存在")

    MIME = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
        "bmp": "image/bmp", "pdf": "application/pdf",
    }
    ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
    mime = MIME.get(ext, "application/octet-stream")

    data: bytes
    if f.storage_key:
        try:
            data = await asyncio.to_thread(object_storage.get, f.storage_key)
        except Exception:
            raise HTTPException(status_code=503, detail="存储不可用")
    elif f.content:
        import base64
        try:
            data = base64.b64decode(f.content)
        except Exception:
            data = f.content.encode("utf-8")
    else:
        raise HTTPException(status_code=404, detail="文件内容不存在")

    from urllib.parse import quote
    ascii_name = f.name.encode("ascii", "ignore").decode() or "file"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(f.name)}"
        },
    )


@router.patch("/{conversation_id}/files/{file_id}", response_model=WorkspaceFileDetail)
async def patch_file(
    conversation_id: uuid.UUID,
    file_id: uuid.UUID,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    f = await db.get(WorkspaceFile, file_id)
    if f is None or f.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    content = payload.get("content", "")
    f = await svc.update_file_content(db, f, content, author=str(user.id))
    return WorkspaceFileDetail(**WorkspaceFileOut.model_validate(f).model_dump(), content=f.content)


@router.get("/{conversation_id}/files/{file_id}/versions", response_model=list[WorkspaceFileVersionOut])
async def list_file_versions(
    conversation_id: uuid.UUID,
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    f = await db.get(WorkspaceFile, file_id)
    if f is None or f.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    versions = await svc.list_file_versions(db, file_id)
    return [WorkspaceFileVersionOut.model_validate(v) for v in versions]


@router.post("/{conversation_id}/files/{file_id}/restore/{version_num}", response_model=WorkspaceFileDetail)
async def restore_file_version(
    conversation_id: uuid.UUID,
    file_id: uuid.UUID,
    version_num: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    f = await db.get(WorkspaceFile, file_id)
    if f is None or f.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    f = await svc.restore_file_version(db, f, version_num, author=str(user.id))
    return WorkspaceFileDetail(**WorkspaceFileOut.model_validate(f).model_dump(), content=f.content)


@router.post("/{conversation_id}/confirm", status_code=200)
async def confirm_action(
    conversation_id: uuid.UUID,
    payload: ConfirmRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    await redis_core.respond_to_confirmation(str(conversation_id), payload.request_id, payload.choice)
    return {"status": "ok"}


@router.post("/{conversation_id}/upload", response_model=WorkspaceFileOut, status_code=201)
async def upload_file(
    conversation_id: uuid.UUID,
    file: UploadFile = FastApiFile(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    raw = await read_upload_capped(file, settings.max_upload_bytes)
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", file.filename or "upload").strip("_. ") or "upload"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "bin"

    processed = await process_upload(
        raw, ext, f"conversations/{convo.id}", name, content_type=file.content_type,
    )

    wf = WorkspaceFile(
        conversation_id=convo.id,
        name=name,
        kind=ext,
        content=processed.content,
        storage_key=processed.storage_key,
        size_bytes=processed.size_bytes,
        created_by_agent=None,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)
    return WorkspaceFileOut.model_validate(wf)


@router.post("/{conversation_id}/extract-items")
async def extract_items(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse the most recent agent messages for project/task creation intent.

    Returns a suggested project name and task list extracted from bullet/numbered
    lists in the conversation. The caller presents this in a confirmation modal
    before actually creating anything.
    """
    import re
    convo = await _require_convo(db, conversation_id, user)
    msgs = await svc.get_messages(db, convo.id)

    agent_texts = [
        m.content.get("text", "") for m in msgs
        if m.role in ("agent", "roundtable") and m.content.get("text")
    ]
    combined = "\n".join(agent_texts[-3:])  # look at last 3 agent turns

    # Extract numbered or bulleted list items as tasks
    task_patterns = [
        r"^\s*(?:\d+[\.\)、]|\*|-|·|•)\s+(.+)$",
    ]
    tasks: list[str] = []
    for line in combined.splitlines():
        for pat in task_patterns:
            m = re.match(pat, line.strip())
            if m:
                task = m.group(1).strip()
                if 3 <= len(task) <= 120:
                    tasks.append(task)
                break

    # Derive a project name from conversation title or first user message
    project_name = convo.title if convo.title and convo.title != "新会话" else ""
    if not project_name and msgs:
        first_user = next((m for m in msgs if m.role == "user"), None)
        if first_user:
            project_name = (first_user.content.get("text") or "")[:40].strip()

    # Deduplicate and cap
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tasks:
        key = t[:50].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    deduped = deduped[:20]

    return {
        "project_name": project_name,
        "tasks": deduped,
        "conversation_id": str(convo.id),
        "team_id": str(convo.team_id) if convo.team_id else None,
    }


@router.post("/{conversation_id}/detect-tasks")
async def detect_tasks(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detect action items from conversation — returns transcript + prompt for the frontend to send."""
    convo = await _require_convo(db, conversation_id, user)
    result = await svc.detect_action_items(db, convo.id)
    return result


@router.post("/{conversation_id}/messages/{message_id}/consolidate")
async def consolidate_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: ConsolidateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Archive a message's text into a project doc or team knowledge (closed loop)."""
    from app.services import team_service

    convo = await _require_convo(db, conversation_id, user)
    message = await svc.get_message(db, message_id)
    if message is None or message.conversation_id != convo.id:
        raise HTTPException(status_code=404, detail="消息不存在")

    project = None
    team = None
    if payload.target == "project_doc":
        if not payload.project_id:
            raise HTTPException(status_code=400, detail="缺少 project_id")
        project = await team_service.get_project(db, payload.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        await team_service.require_permission(db, project.team_id, user.id, "conversation.consolidate")
    elif payload.target == "team_knowledge":
        if not payload.team_id:
            raise HTTPException(status_code=400, detail="缺少 team_id")
        from app.db.models.team import Team
        team = await db.get(Team, payload.team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="团队不存在")
        await team_service.require_permission(db, team.id, user.id, "conversation.consolidate")
        if convo.project_id:
            project = await team_service.get_project(db, convo.project_id)
    else:
        raise HTTPException(status_code=400, detail="target 必须为 project_doc 或 team_knowledge")

    entry = await svc.consolidate_message(
        db, message=message, target=payload.target, name=payload.name,
        actor=user, project=project, team=team,
    )
    return {"id": str(entry.id), "name": entry.name, "target": payload.target}


# ── ACP session control endpoints ──


@router.post("/{conversation_id}/session/fork", response_model=ConversationDetail)
async def fork_session(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fork the ACP session: create a new conversation with copied agent context."""
    from app.db.models.conversation import Conversation

    convo = await _require_convo(db, conversation_id, user)
    if not convo.acp_session_id:
        raise HTTPException(status_code=400, detail="会话没有 ACP session，无法 fork")

    # Create new conversation with same metadata
    new_convo = Conversation(
        title=f"Fork: {convo.title}",
        icon=convo.icon,
        owner_id=user.id,
        team_id=convo.team_id,
        project_id=convo.project_id,
        primary_agent_id=convo.primary_agent_id,
        active_agent_ids=list(convo.active_agent_ids),
        profile_id=convo.profile_id,
        session_mode=convo.session_mode,
    )
    db.add(new_convo)
    await db.flush()

    # Notify runner to fork the ACP session
    from app.core import redis as R
    await R.publish_control(str(conversation_id), {
        "type": "fork",
        "new_conversation_id": str(new_convo.id),
    })

    # Wait for runner response
    resp = await R.wait_for_control_response(str(conversation_id), timeout=15.0)
    new_session_id = resp.get("session_id")
    if new_session_id:
        new_convo.acp_session_id = new_session_id

    await db.commit()
    await db.refresh(new_convo)

    # Return with empty messages
    return ConversationDetail(
        id=new_convo.id,
        title=new_convo.title,
        icon=new_convo.icon,
        primary_agent_id=new_convo.primary_agent_id,
        active_agent_ids=new_convo.active_agent_ids,
        profile_id=new_convo.profile_id,
        acp_session_id=new_convo.acp_session_id,
        session_mode=new_convo.session_mode,
        pinned=new_convo.pinned,
        visibility=new_convo.visibility,
        created_at=new_convo.created_at,
        updated_at=new_convo.updated_at,
        messages=[],
    )


@router.put("/{conversation_id}/session/mode")
async def set_session_mode(
    conversation_id: uuid.UUID,
    body: SetSessionModeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set edit approval mode for the ACP session."""
    convo = await _require_convo(db, conversation_id, user)
    convo.session_mode = body.mode
    await db.commit()
    return {"ok": True, "mode": body.mode}


@router.put("/{conversation_id}/session/model")
async def set_session_model(
    conversation_id: uuid.UUID,
    body: SetSessionModelRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Switch the model for the active ACP session."""
    from app.core import redis as R

    convo = await _require_convo(db, conversation_id, user)
    if not convo.acp_session_id:
        raise HTTPException(status_code=400, detail="会话没有 ACP session")

    await R.publish_control(str(conversation_id), {
        "type": "model",
        "model_id": body.model_id,
    })
    resp = await R.wait_for_control_response(str(conversation_id), timeout=10.0)
    if resp.get("error"):
        raise HTTPException(status_code=502, detail=f"Runner 未响应: {resp['error']}")
    return {"ok": True, "model_id": body.model_id}


# ── Group chat endpoints ────────────────────────────────────────────────


@router.post("/group", status_code=201)
async def create_group(
    payload: GroupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建群聊。"""
    convo = await svc.create_group(
        db, user.id,
        title=payload.title,
        member_user_ids=payload.member_user_ids,
        member_agent_ids=payload.member_agent_ids,
        team_id=payload.team_id,
    )
    members = await svc.get_group_members(db, convo.id)
    return {
        **ConversationOut.model_validate(convo).model_dump(),
        "members": [GroupMemberOut.model_validate(m).model_dump() for m in members],
    }


@router.get("/{conversation_id}/members")
async def get_members(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取群聊成员列表。"""
    convo = await _require_convo(db, conversation_id, user)
    if convo.type != "group":
        raise HTTPException(status_code=400, detail="该会话不是群聊")
    members = await svc.get_group_members(db, conversation_id)
    # Enrich with user names + profile info + live presence.
    from app.db.models.user import User as UserModel
    from app.db.models.agent import Profile as ProfileModel
    human_ids = [str(m.user_id) for m in members if m.user_id]
    profile_ids = [m.profile_id for m in members if m.profile_id]
    # Batch-load profiles.
    profile_map: dict = {}
    if profile_ids:
        prof_rows = (await db.execute(
            select(ProfileModel).where(ProfileModel.id.in_(profile_ids))
        )).scalars().all()
        profile_map = {p.id: p for p in prof_rows}
    # Batch-load users instead of one db.get() per member.
    user_ids = [m.user_id for m in members if m.user_id]
    user_map: dict = {}
    if user_ids:
        user_rows = (await db.execute(
            select(UserModel).where(UserModel.id.in_(user_ids))
        )).scalars().all()
        user_map = {u.id: u for u in user_rows}
    presence = await redis_core.presence_status(human_ids) if human_ids else {}
    result = []
    for m in members:
        data = GroupMemberOut.model_validate(m).model_dump()
        if m.user_id:
            u = user_map.get(m.user_id)
            if u:
                data["user_name"] = u.name or u.email or str(m.user_id)[:8]
            data["presence"] = presence.get(str(m.user_id), "offline")
        if m.profile_id:
            p = profile_map.get(m.profile_id)
            if p:
                data["profile_name"] = p.name
                data["profile_icon"] = p.icon or "sparkle"
                data["profile_color"] = p.color or "#b8852a"
        result.append(data)
    return result


@router.post("/{conversation_id}/members", status_code=201)
async def add_member(
    conversation_id: uuid.UUID,
    payload: AddMemberRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """添加群聊成员。"""
    convo = await _require_convo(db, conversation_id, user)
    if convo.type != "group":
        raise HTTPException(status_code=400, detail="该会话不是群聊")
    if not payload.user_id and not payload.agent_id:
        raise HTTPException(status_code=422, detail="必须指定 user_id 或 agent_id")
    await svc.add_group_member(
        db, conversation_id,
        user_id=payload.user_id,
        agent_id=payload.agent_id,
        role=payload.role,
    )
    return {"ok": True}


@router.delete("/{conversation_id}/members/{member_id}", status_code=204)
async def remove_member(
    conversation_id: uuid.UUID,
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """移除群聊成员（本人可自行退出；移除他人需群管理员权限）。"""
    convo = await _require_convo(db, conversation_id, user)
    if convo.type != "group":
        raise HTTPException(status_code=400, detail="该会话不是群聊")
    if member_id != user.id:
        is_admin = await svc.is_group_admin(db, conversation_id, user.id)
        if not is_admin:
            raise HTTPException(status_code=403, detail="只有群管理员可以移除其他成员")
    # member_id could be user_id — try removing by user_id
    await svc.remove_group_member(db, conversation_id, user_id=member_id)


@router.patch("/{conversation_id}/members/{member_id}", response_model=GroupMemberOut)
async def update_member(
    conversation_id: uuid.UUID,
    member_id: uuid.UUID,
    payload: MemberUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新群成员设置（目前仅 auto_reply，即该 AI 是否在未被 @ 时也自动应答）。"""
    convo = await _require_convo(db, conversation_id, user)
    if convo.type != "group":
        raise HTTPException(status_code=400, detail="该会话不是群聊")
    if payload.auto_reply is not None:
        member = await svc.set_member_auto_reply(
            db, conversation_id, member_id, payload.auto_reply
        )
        if member is None:
            raise HTTPException(status_code=404, detail="成员不存在或不是 AI 助手")
        return GroupMemberOut.model_validate(member)
    raise HTTPException(status_code=422, detail="没有可更新的字段")


# ── Read state · message edit / recall / reactions ──────────────────────────


@router.post("/{conversation_id}/read", response_model=MarkReadResponse)
async def mark_read(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把当前用户在该群聊的已读游标推进到现在。"""
    await _require_convo(db, conversation_id, user)
    last_read = await svc.mark_read(db, conversation_id, user.id)
    return MarkReadResponse(ok=True, last_read_at=last_read)


async def _require_message(db, conversation_id: uuid.UUID, message_id: uuid.UUID, user: User):
    """加载消息并校验属于该会话 + 用户有权访问。"""
    await _require_convo(db, conversation_id, user)
    from app.db.models.conversation import Message as MessageModel
    msg = await db.get(MessageModel, message_id)
    if msg is None or msg.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="消息不存在")
    return msg


@router.patch("/{conversation_id}/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: EditMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """编辑消息（仅作者本人、未撤回的用户消息）。"""
    msg = await _require_message(db, conversation_id, message_id, user)
    if msg.owner_id != user.id or msg.role != "user":
        raise HTTPException(status_code=403, detail="只能编辑自己发送的消息")
    if msg.deleted_at is not None:
        raise HTTPException(status_code=400, detail="消息已撤回")
    msg = await svc.edit_message(db, msg, payload.text)
    return MessageOut.model_validate(msg)


@router.delete("/{conversation_id}/messages/{message_id}", response_model=MessageOut)
async def recall_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """撤回消息（作者本人，或群管理员）。"""
    msg = await _require_message(db, conversation_id, message_id, user)
    is_author = msg.owner_id == user.id
    is_admin = await svc.is_group_admin(db, conversation_id, user.id)
    if not is_author and not is_admin:
        raise HTTPException(status_code=403, detail="无权撤回该消息")
    msg = await svc.recall_message(db, msg)
    return MessageOut.model_validate(msg)


@router.post("/{conversation_id}/messages/{message_id}/reactions", response_model=MessageOut)
async def toggle_reaction(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: ReactionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """切换对某条消息的表情回应。"""
    msg = await _require_message(db, conversation_id, message_id, user)
    msg = await svc.toggle_reaction(db, msg, user.id, payload.emoji)
    return MessageOut.model_validate(msg)


# ── Background subagents (persistent, non-blocking ACP peer sessions) ──────

def _subagent_out(row, unread_count: int = 0) -> SubagentOut:
    return SubagentOut(
        id=row.id,
        parent_conversation_id=row.parent_conversation_id,
        subagent_conversation_id=row.subagent_conversation_id,
        purpose=row.purpose,
        agent_id=row.agent_id,
        profile_id=row.profile_id,
        status=row.status,
        last_active_at=row.last_active_at,
        error_detail=row.error_detail,
        unread_count=unread_count,
        created_at=row.created_at,
    )


async def _require_subagent(db, conversation_id: uuid.UUID, subagent_id: uuid.UUID, user: User):
    row = await subagent_service.get_subagent(db, conversation_id, subagent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="后台任务不存在")
    return row


@router.post("/{conversation_id}/subagents", response_model=SubagentOut, status_code=201)
async def spawn_subagent(
    conversation_id: uuid.UUID,
    payload: SubagentSpawn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    row = await subagent_service.spawn_subagent(
        db, convo, user.id,
        purpose=payload.purpose, initial_prompt=payload.initial_prompt,
        agent_id=payload.agent_id, profile_id=payload.profile_id,
    )
    return _subagent_out(row)


@router.get("/{conversation_id}/subagents", response_model=list[SubagentOut])
async def list_subagents(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    pairs = await subagent_service.list_subagents(db, conversation_id)
    return [_subagent_out(row, unread) for row, unread in pairs]


@router.get("/{conversation_id}/subagents/{subagent_id}", response_model=SubagentOut)
async def get_subagent(
    conversation_id: uuid.UUID,
    subagent_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    row = await _require_subagent(db, conversation_id, subagent_id, user)
    return _subagent_out(row)


@router.post("/{conversation_id}/subagents/{subagent_id}/messages", status_code=202)
async def send_subagent_message(
    conversation_id: uuid.UUID,
    subagent_id: uuid.UUID,
    payload: SubagentSend,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    row = await _require_subagent(db, conversation_id, subagent_id, user)
    await subagent_service.send_to_subagent(row, payload.text)
    return {"status": "queued"}


@router.post("/{conversation_id}/subagents/{subagent_id}/read")
async def mark_subagent_read(
    conversation_id: uuid.UUID,
    subagent_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    row = await _require_subagent(db, conversation_id, subagent_id, user)
    await subagent_service.mark_subagent_read(db, row)
    return {"status": "ok"}


@router.post("/{conversation_id}/subagents/{subagent_id}/stop")
async def stop_subagent(
    conversation_id: uuid.UUID,
    subagent_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_convo(db, conversation_id, user)
    row = await _require_subagent(db, conversation_id, subagent_id, user)
    await subagent_service.request_stop_subagent(row)
    return {"status": "stopping"}
