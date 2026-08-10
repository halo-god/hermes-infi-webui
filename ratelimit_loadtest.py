"""Rate-limit boundary load test (run against the live Redis on :1979).

Validates:
1. per-user send window: exactly `limit` allowed, next one rejected
2. global overflow guard rl:global:msg rejects once its own cap is exceeded
3. monthly usage key TTL aligns to the calendar month
4. login dual-dimension: per-account cap (5) rejects the 6th attempt;
   per-IP cap (15) rejects the 16th; success clears counters
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("HERMES_ENV", "test")

from app.core import ratelimit  # noqa: E402
from app.core.redis import close_redis, get_redis  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2705 {name} {detail}")
    else:
        FAIL += 1
        print(f"  \u274c {name} {detail}")


async def main() -> None:
    uid = "loadtest-user-0001"
    r = get_redis()

    # ── 1. per-user window boundary ──────────────────────────────────────
    print("\n[1] per-user send window (limit = cfg 30)")
    await r.delete(f"rl:msg:{uid}")
    limit = await ratelimit.get_rate_limit()
    allowed_count = 0
    for i in range(limit + 2):
        ok = await ratelimit.allow_send(uid)
        if ok:
            allowed_count += 1
    check(
        f"exactly {limit} allowed, extra rejected",
        allowed_count == limit,
        f"allowed={allowed_count}/{limit + 2}",
    )

    # ── 2. global overflow guard ─────────────────────────────────────────
    print("\n[2] global overflow guard rl:global:msg")
    await r.delete("rl:global:msg")
    g_limit = int(os.environ.get("GLOBAL_CAP", "10"))
    # set a low global cap for the test by directly toggling the key
    await r.set("cfg:rate_limit_per_min", 30)
    hits = 0
    # monkey-patch the module constant path: use a distinct uid so per-user
    # never trips; drive global with its own counter via hit()
    from app.core import ratelimit as rl

    # simulate: 30 uids each sending 1 msg => global counter climbs fast
    g_ok = 0
    for i in range(30):
        u = f"loadtest-user-g{i:04d}"
        await r.delete(f"rl:msg:{u}")
        # force global cap by settings swap (test-only)
        orig = rl.settings.rate_limit_global_per_min
        rl.settings.rate_limit_global_per_min = 10
        try:
            ok = await rl.allow_send(u)
            g_ok += 1 if ok else 0
        finally:
            rl.settings.rate_limit_global_per_min = orig
    check(
        "global cap 10 limits to 10 across 30 distinct users",
        g_ok == 10,
        f"g_ok={g_ok}/30",
    )
    await r.delete("rl:global:msg")
    for i in range(30):
        await r.delete(f"rl:msg:loadtest-user-g{i:04d}")

    # ── 3. monthly TTL aligns to calendar month ──────────────────────────
    print("\n[3] monthly usage key TTL ~ calendar month")
    now = datetime.now(tz=timezone.utc)
    ttl = ratelimit._month_ttl(now)
    if now.month == 12:
        nxt = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    expect = int((nxt - now).total_seconds())
    check(
        "ttl == seconds to month end",
        abs(ttl - expect) < 2,
        f"ttl={ttl} expect~{expect}",
    )

    # ── 4. login dual-dimension ──────────────────────────────────────────
    print("\n[4] login dual-dimension (account 5, ip 15, 900s)")
    ip, acct = "10.9.9.9", "loadtest@acct.dev"
    await r.delete(f"rl:login:ip:{ip}")
    await r.delete(f"rl:login:user:{acct}")
    # account cap trips first
    seen = []
    for i in range(7):
        ok, dim = await ratelimit.check_login(ip, acct)
        seen.append((ok, dim))
    check(
        "account cap 5 rejects 6th",
        seen[5][0] is False and seen[5][1] == "account",
        f"6th={seen[5]}",
    )
    # fresh ip, exhausted account => account still blocks
    ok, dim = await ratelimit.check_login("10.9.9.10", acct)
    check("account block follows across IPs", ok is False and dim == "account", f"={dim}")
    # fresh account, exhausted ip => ip blocks
    await r.delete(f"rl:login:user:{acct}")
    ip2 = "10.9.9.11"
    await r.delete(f"rl:login:ip:{ip2}")
    seen2 = []
    for i in range(17):
        ok, dim = await ratelimit.check_login(ip2, f"loadtest@{i}.dev")
        seen2.append((ok, dim))
    check(
        "ip cap 15 rejects 16th",
        seen2[15][0] is False and seen2[15][1] == "ip",
        f"16th={seen2[15]}",
    )
    # success clears counters
    await ratelimit.clear_login(ip, acct)
    ok, dim = await ratelimit.check_login(ip, acct)
    check("clear_login resets both counters", ok is True and dim is None, f"={ok}")

    # cleanup
    for i in range(17):
        await r.delete(f"rl:login:user:loadtest@{i}.dev")
    await r.delete(f"rl:login:ip:{ip2}")
    await close_redis()

    print(f"\n{'='*40}\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
