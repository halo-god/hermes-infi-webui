"""Per-user notification stream (cross-conversation badges, Slack-style).

The frontend opens a single SSE to GET /me/stream and receives `notify` events
(unread / @-mention) for any group the user belongs to — even when that group's
conversation isn't open. Backed by the capped per-user Redis Stream evt:user:{id}.
Auth is a short-lived media ticket (EventSource cannot set Authorization headers).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_core
from app.db.base import get_db
from app.deps import user_from_media_ticket

router = APIRouter()


@router.get("/me/stream")
async def my_stream(
    request: Request,
    ticket: str = Query(..., description="media ticket (EventSource cannot set headers)"),
    since: str | None = Query(None, description="resume after this stream id"),
    db: AsyncSession = Depends(get_db),
):
    """SSE live stream of the current user's notification events."""
    user = await user_from_media_ticket(ticket, db)
    uid = str(user.id)
    resume_id = request.headers.get("last-event-id") or since

    async def event_gen():
        last_id = resume_id or await redis_core.latest_user_event_id(uid)
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            entries = await redis_core.read_user_events(uid, last_id, block_ms=2000)
            if not entries:
                yield ": keepalive\n\n"
                continue
            for entry_id, data in entries:
                last_id = entry_id
                yield f"id: {entry_id}\ndata: {data}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
