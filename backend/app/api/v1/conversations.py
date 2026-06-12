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
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import metrics, ratelimit
from app.core import redis as redis_core
from app.core.files import read_upload_capped
from app.db.base import async_session_maker, get_db
from app.db.models.user import User
from app.db.models.workspace import WorkspaceFile
from app.deps import get_current_user, user_from_access_token

from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    ConfirmRequest,
    GroupCreate,
    AddMemberRequest,
    GroupMemberOut,
    MessageOut,
    SendMessageRequest,
    SendMessageResponse,
    SetAgentsRequest,
    WorkspaceFileDetail,
    WorkspaceFileOut,
    WorkspaceFileVersionOut,
)
from app.services import conversation_service as svc

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

    result = []
    for m in msgs:
        out = MessageOut.model_validate(m)
        file_list = files_by_msg.get(m.id)
        if file_list:
            out.content = {**out.content, "files": file_list}
        result.append(out)
    return result


async def _require_convo(db, conversation_id: uuid.UUID, user: User):
    convo = await svc.get_conversation(db, conversation_id, user.id)
    if convo is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return convo


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
    """列出用户参与的所有群聊。"""
    convos = await svc.list_group_conversations(db, user.id)
    return [ConversationOut.model_validate(c).model_dump() for c in convos]


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
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.db.models.conversation import Conversation
    res = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.visibility == "shared",
        )
    )
    convo = res.scalar_one_or_none()
    if not convo:
        raise HTTPException(status_code=404, detail="分享链接不存在或已失效")
    msgs = await svc.get_messages(db, convo.id)
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
        user_msg, agent_msg = await svc.dispatch_group(
            db, convo, payload.text, payload.mentions,
            attached_file_ids=payload.attached_file_ids,
            owner_id=user.id,
            skip_agent=payload.skip_agent,
            profile_id_override=payload.profile_id,
        )
    else:
        user_msg, agent_msg = await svc.dispatch(
            db, convo, payload.text,
            attached_file_ids=payload.attached_file_ids,
            owner_id=user.id,
            skip_agent=payload.skip_agent,
            profile_id_override=payload.profile_id,
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
    access_token: str = Query(..., description="access token (EventSource cannot set headers)"),
    since: str | None = Query(None, description="resume after this stream id (e.g. '1700000000000-0')"),
    db: AsyncSession = Depends(get_db),
):
    """SSE live stream of agent events for a conversation.

    Events live in a capped per-conversation Redis Stream, so unlike Pub/Sub
    there is no subscribe-after-publish loss, and reconnects replay: each SSE
    frame carries the stream entry as its `id`, EventSource resends it as the
    Last-Event-ID header on auto-reconnect (the `since` param covers manual
    resume). No DB on the per-event path.
    """
    user = await user_from_access_token(access_token, db)
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
    access_token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Bidirectional channel: client sends {action:'send'|'cancel', text?},
    server relays all conversation events (single + roundtable). Used by the
    roundtable UI; one persistent socket per open conversation."""
    try:
        user = await user_from_access_token(access_token, db)
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
            if action == "send":
                text = (payload.get("text") or "").strip()
                if text:
                    if not await ratelimit.allow_send(str(user_id)):
                        await websocket.send_text(
                            json.dumps({"type": "error", "message_id": "", "detail": "发送过于频繁"})
                        )
                        continue
                    file_ids = payload.get("attached_file_ids") or []
                    p_id = payload.get("profileId") or payload.get("profile_id") or None
                    async with async_session_maker() as db2:
                        c = await svc.get_conversation(db2, conversation_id, user_id)
                        if c:
                            await svc.dispatch(db2, c, text, attached_file_ids=file_ids, owner_id=user_id, profile_id_override=p_id)
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
        from app.core import object_storage

        data = await asyncio.to_thread(object_storage.get, f.storage_key)
        content = data.decode("utf-8", "ignore")
    return WorkspaceFileDetail(
        **WorkspaceFileOut.model_validate(f).model_dump(), content=content
    )


@router.get("/{conversation_id}/files/{file_id}/raw")
async def get_file_raw(
    conversation_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    access_token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import Response

    token: str | None = access_token
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
    if not token:
        raise HTTPException(status_code=401, detail="未认证")
    user = await user_from_access_token(token, db)
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
            from app.core import object_storage
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
    import re as _re
    name = _re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", file.filename or "upload").strip("_. ") or "upload"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "bin"
    TEXT_EXTS = {"md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
                 "yaml", "yml", "toml", "sh", "bash", "log", "xml", "css", "diff", "patch"}

    content: str | None = None
    storage_key: str | None = None

    if ext in TEXT_EXTS:
        content = raw.decode("utf-8", "ignore")
    else:
        import base64
        content = base64.b64encode(raw).decode("ascii")

    wf = WorkspaceFile(
        conversation_id=convo.id,
        name=name,
        kind=ext,
        content=content,
        storage_key=storage_key,
        size_bytes=len(raw),
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


# ── ACP session control endpoints ──

from app.schemas.conversation import SetSessionModeRequest, SetSessionModelRequest


@router.post("/{conversation_id}/session/fork", response_model=ConversationDetail)
async def fork_session(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fork the ACP session: create a new conversation with copied agent context."""
    from app.db.models.conversation import Conversation, Message
    from sqlalchemy import select

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
    # Enrich with user names
    from app.db.models.user import User as UserModel
    result = []
    for m in members:
        data = GroupMemberOut.model_validate(m).model_dump()
        if m.user_id:
            u = await db.get(UserModel, m.user_id)
            if u:
                data["user_name"] = u.name or u.email or str(m.user_id)[:8]
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
    """移除群聊成员。"""
    convo = await _require_convo(db, conversation_id, user)
    if convo.type != "group":
        raise HTTPException(status_code=400, detail="该会话不是群聊")
    # member_id could be user_id — try removing by user_id
    await svc.remove_group_member(db, conversation_id, user_id=member_id)
