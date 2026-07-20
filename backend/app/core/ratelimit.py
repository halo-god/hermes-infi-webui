"""Redis fixed-window rate limiting + monthly usage counters.

The enforced per-minute send limit is read from Redis key `cfg:rate_limit_per_min`
(set by admins via system settings) with an env default fallback — so limits are
tunable at runtime without a redeploy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

_RATE_CFG_KEY = "cfg:rate_limit_per_min"


async def get_rate_limit() -> int:
    val = await get_redis().get(_RATE_CFG_KEY)
    try:
        return int(val) if val is not None else settings.rate_limit_per_min
    except (TypeError, ValueError):
        return settings.rate_limit_per_min


async def set_rate_limit(per_min: int) -> None:
    await get_redis().set(_RATE_CFG_KEY, max(1, int(per_min)))


async def hit(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    """Increment a fixed-window counter. Returns (allowed, remaining)."""
    r = get_redis()
    cur = await r.incr(key)
    if cur == 1:
        await r.expire(key, window_seconds)
    return cur <= limit, max(0, limit - cur)


async def incr_monthly_messages(user_id: str) -> int:
    """Track per-user monthly message volume (soft quota / usage display)."""
    month = datetime.now(tz=timezone.utc).strftime("%Y%m")
    key = f"usage:msg:{user_id}:{month}"
    r = get_redis()
    cur = await r.incr(key)
    if cur == 1:
        await r.expire(key, 60 * 60 * 24 * 40)  # ~40 days
    return cur


async def monthly_messages(user_id: str) -> int:
    month = datetime.now(tz=timezone.utc).strftime("%Y%m")
    val = await get_redis().get(f"usage:msg:{user_id}:{month}")
    return int(val) if val else 0


async def allow_send(user_id: str) -> bool:
    """Per-minute send gate; bumps monthly usage when allowed."""
    limit = await get_rate_limit()
    allowed, _ = await hit(f"rl:msg:{user_id}", limit, 60)
    if allowed:
        await incr_monthly_messages(user_id)
    else:
        logger.warning("Rate limit hit: user %s exceeded %d msg/min", user_id[:12], limit)
    return allowed
