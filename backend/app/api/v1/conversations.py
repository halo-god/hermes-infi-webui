"""Conversations: CRUD, send message, SSE stream, cancel, workspace files."""
from __future__ import annotations

import asyncio
import json
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

from app.core import metrics, ratelimit
from app.core import redis as redis_core
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


async def _require_convo(db, conversation_id: uuid.UUID, user: User):
    convo = await svc.get_conversation(db, conversation_id, user.id)
    if convo is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return convo


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    q: str | None = Query(None),
    pinned: bool = Query(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await svc.list_conversations(db, user.id, q=q, pinned_only=pinned)


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


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    convo = await _require_convo(db, conversation_id, user)
    msgs = await svc.get_messages(db, convo.id)
    return ConversationDetail(
        **ConversationOut.model_validate(convo).model_dump(),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


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
    user_msg, agent_msg = await svc.dispatch(
        db, convo, payload.text, attached_file_ids=payload.attached_file_ids, owner_id=user.id
    )
    return SendMessageResponse(
        user_message=MessageOut.model_validate(user_msg),
        agent_message=MessageOut.model_validate(agent_msg),
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


@router.get("/{conversation_id}/stream")
async def stream(
    conversation_id: uuid.UUID,
    request: Request,
    access_token: str = Query(..., description="access token (EventSource cannot set headers)"),
    db: AsyncSession = Depends(get_db),
):
    """SSE live stream of agent events for a conversation.

    Performance: pure Redis Pub/Sub fan-out, no DB on the per-event path,
    each event flushed immediately. EventSource auto-reconnects on drop.
    """
    user = await user_from_access_token(access_token, db)
    convo = await svc.get_conversation(db, conversation_id, user.id)
    if convo is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    channel = redis_core.conv_channel(str(conversation_id))

    async def event_gen():
        pubsub = redis_core.get_redis().pubsub()
        await pubsub.subscribe(channel)
        try:
            yield ": connected\n\n"  # prelude opens the stream promptly
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if msg is None:
                    yield ": keepalive\n\n"  # heartbeat
                    continue
                data = msg["data"]
                yield f"data: {data}\n\n"
                # Close the stream once the turn is done.
                try:
                    if json.loads(data).get("type") == "done":
                        break
                except (TypeError, ValueError):
                    pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

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
    convo = await svc.get_conversation(db, conversation_id, user.id)
    if convo is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    channel = redis_core.conv_channel(str(conversation_id))
    pubsub = redis_core.get_redis().pubsub()
    await pubsub.subscribe(channel)

    async def pump_out():
        async for msg in pubsub.listen():
            if msg.get("type") == "message":
                await websocket.send_text(msg["data"])

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
                    if not await ratelimit.allow_send(str(user.id)):
                        await websocket.send_text(
                            json.dumps({"type": "error", "message_id": "", "detail": "发送过于频繁"})
                        )
                        continue
                    file_ids = payload.get("attached_file_ids") or []
                    async with async_session_maker() as db2:
                        c = await svc.get_conversation(db2, conversation_id, user.id)
                        if c:
                            await svc.dispatch(db2, c, text, attached_file_ids=file_ids)
            elif action == "cancel":
                await redis_core.request_cancel(str(conversation_id))
    except WebSocketDisconnect:
        pass
    finally:
        out_task.cancel()
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


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

    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{f.name}"'},
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
    raw = await file.read()
    name = file.filename or "upload"
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
