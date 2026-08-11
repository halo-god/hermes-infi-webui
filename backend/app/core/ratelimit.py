"""Redis fixed-window rate limiting + monthly usage counters.

The enforced per-minute send limit is read from Redis key `cfg:rate_limit_per_min`
(set by admins via system settings) with an env default fallback — so limits are
tunable at runtime without a redeploy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    OutOfMemoryError,
    ReadOnlyError,
    TimeoutError as RedisTimeoutError,
)

from app.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# Only "Redis temporarily unavailable" errors should trigger the degrade path.
# ResponseError (WRONGTYPE, Lua script errors, wrong key type) is a CODE BUG and
# must bubble up so it's visible — swallowing it silently disables rate limiting
# (fail-open) or locks everyone out (fail-closed) with no trace. See skill
# redis-rate-limiting checklist #7/#9.
REDIS_UNAVAILABLE = (
    RedisConnectionError,
    RedisTimeoutError,
    ReadOnlyError,
    OutOfMemoryError,
)

_RATE_CFG_KEY = "cfg:rate_limit_per_min"

# Atomic fixed-window increment: INCR + first-time EXPIRE in one Lua round trip.
# Fixes the race where a crash between INCR and EXPIRE left a key with no TTL
# (permanent counter / silent unbounded growth).
_INCR_EXPIRE_LUA = """
local cur = redis.call('INCR', KEYS[1])
if cur == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return cur
"""


def _month_ttl(now: datetime | None = None) -> int:
    """Seconds until the end of the current UTC month, so usage keys align to the
    natural calendar month instead of a fixed ~40 days (which drifts every month)."""
    now = now or datetime.now(tz=timezone.utc)
    if now.month == 12:
        nxt = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return int((nxt - now).total_seconds())


async def get_rate_limit() -> int:
    val = await get_redis().get(_RATE_CFG_KEY)
    try:
        return int(val) if val is not None else settings.rate_limit_per_min
    except (TypeError, ValueError):
        return settings.rate_limit_per_min


async def set_rate_limit(per_min: int) -> None:
    await get_redis().set(_RATE_CFG_KEY, max(1, int(per_min)))


async def hit(key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
    """Increment a fixed-window counter atomically. Returns (allowed, remaining)."""
    r = get_redis()
    cur = int(await r.eval(_INCR_EXPIRE_LUA, 1, key, window_seconds))
    return cur <= limit, max(0, limit - cur)


async def incr_monthly_messages(user_id: str) -> int:
    """Track per-user monthly message volume (soft quota / usage display)."""
    month = datetime.now(tz=timezone.utc).strftime("%Y%m")
    key = f"usage:msg:{user_id}:{month}"
    r = get_redis()
    cur = int(await r.eval(_INCR_EXPIRE_LUA, 1, key, _month_ttl()))
    return cur


async def monthly_messages(user_id: str) -> int:
    month = datetime.now(tz=timezone.utc).strftime("%Y%m")
    val = await get_redis().get(f"usage:msg:{user_id}:{month}")
    return int(val) if val else 0


async def allow_send(user_id: str) -> bool:
    """Per-minute send gate (per-user + global overflow); bumps monthly usage when allowed.

    Fail-open on Redis errors: if Redis is down, the messaging path must not be
    blocked — the per-user cap is an abuse guard, not a business rule.
    """
    try:
        limit = await get_rate_limit()  # must be INSIDE try: first await that
        # can raise; if it sat outside, a Redis outage would bubble the
        # exception past this try and the fail-open return below would be dead
        # code (observed: send path 500s instead of passing).
        allowed, _ = await hit(f"rl:msg:{user_id}", limit, 60)
        if allowed:
            # Global overflow guard: caps total platform send rate so one user
            # (or a burst across many users) cannot saturate the queue even when
            # each individual stays under their per-user cap.
            allowed, _ = await hit("rl:global:msg", settings.rate_limit_global_per_min, 60)
            if not allowed:
                logger.warning("Global rate limit hit: platform exceeds %d msg/min", settings.rate_limit_global_per_min)
        else:
            logger.warning("Rate limit hit: user %s exceeded %d msg/min", user_id[:12], limit)
        if allowed:
            await incr_monthly_messages(user_id)
        return allowed
    except REDIS_UNAVAILABLE:  # noqa: BLE001
        logger.exception("Rate limit check failed, failing open (allow send)")
        return True


async def check_login(ip: str | None, username: str) -> tuple[bool, str | None]:
    """Dual-dimension login guard: per-IP and per-account, both 15-min windows.

    Fail-closed: if Redis is down, deny login — better to temporarily lock out
    everyone than to open the door to unbounded brute-force.

    Returns (allowed, denied_dimension). denied_dimension is "ip" or "account"
    when blocked, else None.
    """
    if ip:
        try:
            ok_ip, _ = await hit(
                f"rl:login:ip:{ip}", settings.login_ip_limit, settings.login_window_seconds
            )
        except REDIS_UNAVAILABLE:  # noqa: BLE001
            logger.exception("Login rate-limit check failed (ip), failing closed")
            ok_ip = False
        if not ok_ip:
            return False, "ip"
    if username:
        try:
            ok_user, _ = await hit(
                f"rl:login:user:{username.strip().lower()}",
                settings.login_user_limit,
                settings.login_window_seconds,
            )
        except REDIS_UNAVAILABLE:  # noqa: BLE001
            logger.exception("Login rate-limit check failed (account), failing closed")
            ok_user = False
        if not ok_user:
            return False, "account"
    return True, None


async def clear_login(ip: str | None, username: str) -> None:
    """Reset login counters on successful auth so a legit user is never penalized
    by their own prior failed attempts.

    Account dimension only (ip param kept for call-site compatibility but not
    cleared): under shared IP/NAT, deleting the IP key lets any legit credential
    holder reset the whole IP's brute-force counter, defeating the per-IP guard.
    The IP key expires on its own after the 15-min window.
    """
    r = get_redis()
    try:
        if username:
            await r.delete(f"rl:login:user:{username.strip().lower()}")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to clear login counters (non-fatal)")
