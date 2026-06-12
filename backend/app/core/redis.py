"""Async Redis client + token-blacklist helpers (for logout / device revocation)."""
from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            protocol=2,            # RESP2: fixes xreadgroup block-timeout under RESP3
            socket_timeout=10,      # generous socket timeout for blocking reads
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ── Refresh-token blacklist (revocation) ──
_BLACKLIST_PREFIX = "jwt:blacklist:"


async def blacklist_jti(jti: str, ttl_seconds: int) -> None:
    await get_redis().set(f"{_BLACKLIST_PREFIX}{jti}", "1", ex=max(ttl_seconds, 1))


async def is_blacklisted(jti: str) -> bool:
    return bool(await get_redis().exists(f"{_BLACKLIST_PREFIX}{jti}"))


# ── ACP prompt queue (Redis Stream) + live event stream (per conversation) ──
import json as _json  # noqa: E402

from app.config import settings  # noqa: E402

# Live events are stored in a capped per-conversation Redis Stream (not Pub/Sub):
# Streams replay, so a subscriber that connects after events were published —
# or an SSE client reconnecting with Last-Event-ID — never loses tokens.
EVENT_STREAM_MAXLEN = 2000
EVENT_STREAM_TTL = 86_400  # seconds


def conv_stream(conversation_id: str) -> str:
    return f"evt:conv:{conversation_id}"


def cancel_key(conversation_id: str) -> str:
    return f"acp:cancel:{conversation_id}"


async def enqueue_prompt(payload: dict) -> str:
    """Push a prompt task onto the runner's Redis Stream. Returns entry id."""
    return await get_redis().xadd(settings.acp_stream, {"data": _json.dumps(payload)})


async def publish_event(conversation_id: str, event: dict) -> None:
    """Append a streaming event to the conversation's live stream (hot path).

    Retries once: a transient Redis blip must not permanently drop tokens.
    """
    event.setdefault("conversation_id", conversation_id)
    payload = _json.dumps(event)
    key = conv_stream(conversation_id)
    for attempt in (1, 2):
        try:
            r = get_redis()
            pipe = r.pipeline()
            pipe.xadd(key, {"data": payload}, maxlen=EVENT_STREAM_MAXLEN, approximate=True)
            pipe.expire(key, EVENT_STREAM_TTL)
            await pipe.execute()
            return
        except Exception:
            if attempt == 2:
                raise
            await _asyncio.sleep(0.05)


async def latest_event_id(conversation_id: str) -> str:
    """Return the id of the newest event in the conversation stream ('0-0' if empty)."""
    entries = await get_redis().xrevrange(conv_stream(conversation_id), count=1)
    return entries[0][0] if entries else "0-0"


async def read_events(
    conversation_id: str, last_id: str, block_ms: int = 8000, count: int = 256
) -> list[tuple[str, str]]:
    """Blocking read of events after `last_id`. Returns [(entry_id, json_str), ...]."""
    resp = await get_redis().xread(
        {conv_stream(conversation_id): last_id}, count=count, block=block_ms
    )
    out: list[tuple[str, str]] = []
    for _key, entries in resp or []:
        for entry_id, fields in entries:
            data = fields.get("data")
            if data is not None:
                out.append((entry_id, data))
    return out


async def request_cancel(conversation_id: str) -> None:
    await get_redis().set(cancel_key(conversation_id), "1", ex=120)


async def is_cancelled(conversation_id: str) -> bool:
    return bool(await get_redis().exists(cancel_key(conversation_id)))


async def clear_cancel(conversation_id: str) -> None:
    await get_redis().delete(cancel_key(conversation_id))


# ── AI Confirmation requests ──
import asyncio as _asyncio  # noqa: E402
from contextlib import suppress as _suppress  # noqa: E402


def confirm_key(conversation_id: str, request_id: str) -> str:
    return f"confirm:{conversation_id}:{request_id}"


async def wait_for_confirmation(
    conversation_id: str, request_id: str, timeout: int = 300, *, cancel_check: bool = False
) -> dict:
    """Wait for the user's answer to a confirmation/clarify modal.

    With cancel_check=True the wait also watches the conversation's cancel flag,
    so hitting 取消 unblocks a clarify-waiting agent immediately.
    """
    key = confirm_key(conversation_id, request_id)
    pubsub_key = f"confirm_notify:{conversation_id}"
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(pubsub_key)
    try:
        # Check if already present
        val = await get_redis().get(key)
        if val:
            await get_redis().delete(key)
            return _json.loads(val)
        # Wait via pub/sub notification (much more efficient than polling)
        deadline = _asyncio.get_event_loop().time() + timeout
        while _asyncio.get_event_loop().time() < deadline:
            remaining = deadline - _asyncio.get_event_loop().time()
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=min(remaining, 1.0))
            if msg and msg["type"] == "message":
                val = await get_redis().get(key)
                if val:
                    await get_redis().delete(key)
                    return _json.loads(val)
            if cancel_check and await is_cancelled(conversation_id):
                return {"choice": "已取消"}
        return {"choice": "超时"}
    finally:
        with _suppress(Exception):
            await pubsub.unsubscribe(pubsub_key)
        with _suppress(Exception):
            await pubsub.aclose()


async def respond_to_confirmation(conversation_id: str, request_id: str, choice: str) -> None:
    key = confirm_key(conversation_id, request_id)
    await get_redis().set(key, _json.dumps({"choice": choice}), ex=300)
    # Notify waiters via pub/sub
    await get_redis().publish(f"confirm_notify:{conversation_id}", request_id)


# ── Clarify bridge (runner ↔ agent clarify_callback), protocol v2 ──
# The agent RPUSHes its request onto a per-session LIST and BLPOPs the
# per-clarify response LIST. BLPOP returns immediately even when the answer
# was pushed *before* the agent started waiting — race-free by construction.


def clarify_req_key(session_id: str) -> str:
    return f"hermes:clarify:req:{session_id}"


def clarify_resp_key(session_id: str, clarify_id: str) -> str:
    return f"hermes:clarify:resp:{session_id}:{clarify_id}"


# ── Agent memory consolidation (做梦整理记忆) ──
# The status key doubles as the run lock (SET NX on enqueue; runner overwrites
# it with done/error). The cooldown key gates non-admin re-triggers via its TTL.


def mem_consolidate_status_key(user_id: str) -> str:
    return f"mem:consolidate:status:{user_id}"


def mem_consolidate_cooldown_key(user_id: str) -> str:
    return f"mem:consolidate:cooldown:{user_id}"


# ── User presence (online/offline) ──
_PRESENCE_PREFIX = "presence:"
_PRESENCE_TTL = 60  # seconds; heartbeat every 30s keeps it alive


async def presence_heartbeat(user_id: str) -> None:
    """Refresh user's online presence. Call every ~30s from frontend."""
    await get_redis().set(f"{_PRESENCE_PREFIX}{user_id}", "online", ex=_PRESENCE_TTL)


async def presence_status(user_ids: list[str]) -> dict[str, str]:
    """Batch query presence for multiple users. Returns {user_id: 'online'|'offline'}."""
    if not user_ids:
        return {}
    r = get_redis()
    pipe = r.pipeline()
    for uid in user_ids:
        pipe.exists(f"{_PRESENCE_PREFIX}{uid}")
    results = await pipe.execute()
    return {uid: "online" if exists else "offline" for uid, exists in zip(user_ids, results)}


# ── ACP session control channel ──
_CONTROL_STREAM = "acp:control"


async def publish_control(conversation_id: str, data: dict) -> None:
    """Send a control message to the runner (fork/model/mode)."""
    import json
    data["conversation_id"] = conversation_id
    await get_redis().xadd(_CONTROL_STREAM, {"data": json.dumps(data)})


async def wait_for_control_response(conversation_id: str, timeout: float = 15.0) -> dict:
    """Wait for runner's response to a control request."""
    import asyncio
    import json
    channel = f"chan:control:{conversation_id}"
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)
    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg["type"] == "message":
                return json.loads(msg["data"])
            await asyncio.sleep(0.1)
        return {"error": "timeout"}
    finally:
        with _suppress(Exception):
            await pubsub.unsubscribe(channel)
        with _suppress(Exception):
            await pubsub.aclose()
