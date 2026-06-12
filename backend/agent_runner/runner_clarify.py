"""Clarify protocol: LIST + BLPOP, race-free."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.conversation import Message

logger = logging.getLogger("hermes.runner")


async def pop_clarify_request(sid: str) -> dict | None:
    """Atomically consume one pending clarify request, if any."""
    r = R.get_redis()
    val = await r.lpop(R.clarify_req_key(sid))
    if val is None and settings.clarify_protocol == "dual":
        val = await r.getdel(f"hermes:clarify_pending:{sid}")
    if not val:
        return None
    try:
        return json.loads(val)
    except (TypeError, ValueError):
        logger.warning("Malformed clarify request for sid=%s: %r", sid[:8], val)
        return None


async def deliver_clarify_response(sid: str, clarify_id: str, choice: str) -> bool:
    """Unblock the agent's clarify_callback with the chosen answer."""
    try:
        r = R.get_redis()
        resp_key = R.clarify_resp_key(sid, clarify_id)
        pipe = r.pipeline()
        pipe.rpush(resp_key, choice)
        pipe.expire(resp_key, 60)
        if settings.clarify_protocol == "dual":
            pipe.set(f"hermes:clarify_response:{sid}:{clarify_id}", choice, ex=60)
            pipe.publish(f"hermes:clarify_notify:{sid}", clarify_id)
        await pipe.execute()
        return True
    except Exception:
        logger.exception("Failed to deliver clarify response sid=%s id=%s", sid[:8], clarify_id[:8])
        return False


async def handle_clarify_request(
    conversation_id: str, message_id: str, acc: dict, sid: str, data: dict,
    bg_tasks: set,
) -> None:
    """Present every clarify request to the user via the confirmation modal.

    Auto-resolve has been removed — all questions require human input.
    """
    clarify_id = data.get("clarify_id") or uuid.uuid4().hex[:12]
    question = data.get("question") or "需要确认"
    options = data.get("options") or ["继续", "跳过"]

    await _record_clarify(message_id, acc, {
        "id": clarify_id, "question": question, "options": options,
        "status": "pending", "ts": datetime.now(tz=timezone.utc).isoformat(),
    })
    req_payload = {
        "id": clarify_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "question": question,
        "questions": [{"question": question, "options": options, "allow_free_text": True}],
        "options": options,
    }
    await R.publish_event(
        conversation_id,
        {"type": "confirmation_request", "message_id": message_id, "request": req_payload},
    )
    logger.info("Clarify request, sent confirmation_request: %s (sid=%s)", clarify_id, sid[:8])

    t = asyncio.create_task(
        _wait_and_unblock_clarify(
            conversation_id, clarify_id, sid=sid, message_id=message_id, acc=acc
        )
    )
    bg_tasks.add(t)
    t.add_done_callback(bg_tasks.discard)


async def _wait_and_unblock_clarify(
    conversation_id: str, clarify_id: str, *,
    sid: str, message_id: str | None = None, acc: dict | None = None,
) -> None:
    """Wait for the user's modal answer (or cancel/timeout), then unblock
    the agent's clarify_callback and persist the outcome."""
    try:
        resp = await R.wait_for_confirmation(
            conversation_id, clarify_id,
            timeout=settings.clarify_timeout_seconds, cancel_check=True,
        )
        choice = resp.get("choice", "超时")
    except Exception:
        logger.warning("Clarify wait failed for %s", clarify_id[:8], exc_info=True)
        choice = "超时"
    logger.info("Clarify response for %s: %s", clarify_id[:8], choice)

    try:
        await R.publish_event(
            conversation_id,
            {"type": "confirmation_response", "request_id": clarify_id, "choice": choice},
        )
    except Exception:
        logger.warning("Failed to publish confirmation_response", exc_info=True)

    if not await deliver_clarify_response(sid, clarify_id, choice):
        await asyncio.sleep(0.5)
        await deliver_clarify_response(sid, clarify_id, choice)

    if message_id and acc is not None:
        status = {"已取消": "cancelled", "超时": "timeout"}.get(choice, "answered")
        await _update_clarify(message_id, acc, clarify_id, status, choice)


async def _record_clarify(message_id: str, acc: dict, entry: dict) -> None:
    acc.setdefault("clarifies", []).append(entry)
    await _write_clarifies(message_id, acc["clarifies"])


async def _update_clarify(
    message_id: str, acc: dict, clarify_id: str, status: str, choice: str
) -> None:
    for e in acc.get("clarifies", []):
        if e.get("id") == clarify_id:
            e["status"] = status
            e["choice"] = choice
    await _write_clarifies(message_id, acc.get("clarifies", []))


async def _write_clarifies(message_id: str, clarifies: list[dict]) -> None:
    try:
        async with async_session_maker() as db:
            msg = await db.get(Message, uuid.UUID(message_id))
            if msg:
                msg.content = {**(msg.content or {}), "clarifies": clarifies}
                await db.commit()
    except Exception:
        logger.warning("Failed to persist clarifies for %s", message_id[:8], exc_info=True)
