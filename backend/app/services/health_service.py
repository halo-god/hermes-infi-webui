"""Admin 健康检查 (health check) service.

Two tiers:
- core: fast (<2s) liveness of api/postgres/redis/runner(+warm pool)/minio.
- dependencies: slower probes (identity providers, MCP servers) plus the
  per-Profile ACP availability derived from the runner's warm-pool report.
"""
from __future__ import annotations

import asyncio
import os
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import object_storage, redis as redis_core
from app.db.base import async_session_maker
from app.db.models.agent import Profile

_DEP_TIMEOUT = 5  # per-dependency probe timeout (seconds)

# ── MCP HTTP probe (shared with admin.py mcp-server status) ──


async def probe_mcp_url(name: str, url: str | None) -> dict:
    """Reachability probe for an MCP server URL, with SSRF protection
    (private/loopback/link-local IPs are blocked). Returns a status dict."""
    if not url:
        return {"name": name, "status": "no_url", "reachable": False}

    import aiohttp
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"name": name, "status": "invalid_scheme", "reachable": False}
    hostname = parsed.hostname or ""
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for family, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return {"name": name, "status": "blocked", "reachable": False,
                        "error": "URL points to a private/loopback address"}
    except (socket.gaierror, ValueError):
        pass  # Let the request proceed; DNS resolution will fail naturally

    try:
        timeout = aiohttp.ClientTimeout(total=_DEP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{url.rstrip('/')}/health") as resp:
                return {
                    "name": name,
                    "status": "ok" if resp.status < 500 else "error",
                    "reachable": resp.status < 500,
                    "http_status": resp.status,
                }
    except Exception as e:  # noqa: BLE001
        return {"name": name, "status": "unreachable", "reachable": False, "error": str(e)}


# ── Core checks ──


async def _check_api() -> dict:
    import psutil

    proc = psutil.Process()
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - proc.create_time()),
    }


async def _check_postgres() -> dict:
    from sqlalchemy import text

    start = time.perf_counter()
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ok", "latency_ms": round((time.perf_counter() - start) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": exc.__class__.__name__}


async def _check_redis() -> dict:
    start = time.perf_counter()
    try:
        await redis_core.get_redis().ping()
        return {"status": "ok", "latency_ms": round((time.perf_counter() - start) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": exc.__class__.__name__}


async def _check_runner() -> dict:
    """Runner liveness via the distributed lock + warm-pool stats snapshot."""
    r = redis_core.get_redis()
    result: dict = {"status": "down"}
    try:
        exists = await r.exists("hermes:runner:lock")
        ttl = await r.ttl("hermes:runner:lock") if exists else -2
    except Exception:  # noqa: BLE001
        exists, ttl = 0, -2
    if exists:
        result["status"] = "ok"
        result["ttl_seconds"] = max(0, ttl)
    pool = await redis_core.get_runner_pool_stats()
    if pool:
        result["pool"] = {**pool, "status": "ok"}
    else:
        result["pool"] = {"status": "stale", "target": 0, "per_profile": {}}
    return result


async def _check_minio() -> dict:
    start = time.perf_counter()
    try:
        from app.config import settings

        await asyncio.to_thread(_minio_head_bucket, settings.minio_bucket)
        return {"status": "ok", "latency_ms": round((time.perf_counter() - start) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": exc.__class__.__name__}


def _minio_head_bucket(bucket: str) -> None:
    object_storage._ensure_bucket()
    object_storage._get_client().head_bucket(Bucket=bucket)


async def check_core() -> dict:
    """Fast liveness of the five core components (parallel, <2s)."""
    api, pg, redis, runner, minio = await asyncio.gather(
        _check_api(), _check_postgres(), _check_redis(), _check_runner(), _check_minio(),
    )
    return {"api": api, "postgres": pg, "redis": redis, "runner": runner, "minio": minio}


# ── Dependency checks ──


async def _check_identity_providers(db: AsyncSession) -> list[dict]:
    from app.db.models.identity import IdentityProvider

    rows = (
        await db.execute(
            select(IdentityProvider).where(IdentityProvider.enabled.is_(True))
        )
    ).scalars().all()
    out: list[dict] = []
    for p in rows:
        try:
            result = await asyncio.wait_for(
                _test_provider(db, p), timeout=_DEP_TIMEOUT
            )
            out.append({
                "id": p.id, "label": p.label,
                "status": "ok" if result.get("ok") else "error",
                "message": result.get("message") or "",
            })
        except asyncio.TimeoutError:
            out.append({"id": p.id, "label": p.label, "status": "error", "message": "探测超时"})
    return out


async def _test_provider(db: AsyncSession, provider) -> dict:
    from app.services import identity_service

    return await identity_service.test_provider(db, provider.id)


async def _check_mcp_servers(db: AsyncSession) -> list[dict]:
    from app.services import settings_service

    row = await settings_service.get(db)
    servers: list[dict] = (row.data or {}).get("mcp_servers", [])
    enabled = [s for s in servers if s.get("enabled", True)]
    if not enabled:
        return []
    results = await asyncio.gather(
        *[
            asyncio.wait_for(
                probe_mcp_url(s.get("name", ""), s.get("url") or s.get("base_url")),
                timeout=_DEP_TIMEOUT,
            )
            for s in enabled
        ],
        return_exceptions=True,
    )
    out: list[dict] = []
    for s, r in zip(enabled, results):
        if isinstance(r, Exception):
            out.append({"name": s.get("name", ""), "status": "unreachable",
                        "reachable": False, "error": r.__class__.__name__})
        else:
            out.append(r)
    return out


async def _check_profiles_acp(db: AsyncSession) -> list[dict]:
    """Per-Profile ACP availability from the runner's warm-pool report.

    The runner warms one ACP client per active Profile at startup; a successful
    spawn means the Profile's HERMES_HOME / LLM provider is usable.
    """
    pool = await redis_core.get_runner_pool_stats()
    per_profile = (pool or {}).get("per_profile", {}) or {}

    rows = (
        await db.execute(select(Profile).where(Profile.is_active.is_(True)))
    ).scalars().all()
    out: list[dict] = []
    for p in rows:
        pool_key = "default"
        if p.path:
            pool_key = os.path.dirname(os.path.expanduser(p.path))
        entry = per_profile.get(pool_key) or {}
        if entry.get("ok"):
            status, error = "ok", entry.get("error")
        elif entry.get("error"):
            status, error = "error", entry.get("error")
        else:
            status, error = "unknown", "未预热（runner 未启动或该 Profile 未预热）"
        out.append({
            "profile_id": str(p.id),
            "name": p.name or p.handle or "",
            "handle": p.handle or "",
            "acp_status": status,
            "warm_count": int(entry.get("warm") or 0),
            "error": error,
        })
    return out


async def check_dependencies(db: AsyncSession) -> dict:
    identity, mcp, profiles = await asyncio.gather(
        _check_identity_providers(db), _check_mcp_servers(db), _check_profiles_acp(db),
    )
    return {"identity": identity, "mcp": mcp, "profiles_acp": profiles}


# ── Report ──


async def health_report(db: AsyncSession, *, deep: bool = False) -> dict:
    core = await check_core()
    core_ok = all(c.get("status") == "ok" for c in core.values())
    report: dict = {
        "timestamp": time.time(),
        "overall": "ok" if core_ok else "error",
        "core": core,
    }
    if deep:
        deps = await check_dependencies(db)
        report["dependencies"] = deps
        # only dependencies failed → degraded
        if core_ok and any(
            d.get("status") != "ok" for group in deps.values() for d in group
        ):
            report["overall"] = "degraded"
    return report
