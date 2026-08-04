"""Admin 健康检查 (health check) — core components + dependency probes."""
from __future__ import annotations

import uuid

import pytest

from app.core import redis as redis_core


async def _make_admin_headers(client, db) -> dict:
    from app.core.security import create_token, hash_password
    from app.db.models.user import User

    uniq = uuid.uuid4().hex[:8]
    admin = User(id=uuid.uuid4(), email=f"hc-admin-{uniq}@hermes.io", name="健康检查管理员",
                 password_hash=hash_password("Admin@1234"), is_active=True, role="admin")
    db.add(admin)
    await db.flush()
    return {"Authorization": f"Bearer {create_token(str(admin.id), 'access')[0]}"}


@pytest.mark.asyncio
async def test_health_core(client, db):
    from sqlalchemy import text
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL not reachable")

    admin_headers = await _make_admin_headers(client, db)
    r = await client.get("/api/v1/admin/health", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["core"].keys()) == {"api", "postgres", "redis", "runner", "minio"}
    assert body["core"]["api"]["status"] == "ok"
    assert body["core"]["postgres"]["status"] == "ok"
    assert body["core"]["redis"]["status"] == "ok"
    # runner: lock present (runner running) or down — either shape is valid
    assert body["core"]["runner"]["status"] in ("ok", "down")
    assert "pool" in body["core"]["runner"]
    # minio: storage backend is minio in this env → ok/error both valid shapes
    assert body["core"]["minio"]["status"] in ("ok", "error")
    assert body["overall"] in ("ok", "error")
    # shallow mode: no dependencies section
    assert "dependencies" not in body


@pytest.mark.asyncio
async def test_health_deep_structure(client, db):
    from sqlalchemy import text
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL not reachable")

    admin_headers = await _make_admin_headers(client, db)
    r = await client.get("/api/v1/admin/health", params={"deep": "true"}, headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    deps = body["dependencies"]
    assert set(deps.keys()) == {"identity", "mcp", "profiles_acp"}
    # identity: array of providers (may be empty)
    assert isinstance(deps["identity"], list)
    for p in deps["identity"]:
        assert {"id", "label", "status", "message"} <= set(p.keys())
        assert p["status"] in ("ok", "error")
    # mcp: array
    assert isinstance(deps["mcp"], list)
    # profiles_acp: at least the active profiles with valid acp_status
    assert isinstance(deps["profiles_acp"], list)
    for p in deps["profiles_acp"]:
        assert {"profile_id", "name", "acp_status", "warm_count"} <= set(p.keys())
        assert p["acp_status"] in ("ok", "error", "unknown")


@pytest.mark.asyncio
async def test_health_requires_admin(client, auth_headers):
    r = await client.get("/api/v1/admin/health", headers=auth_headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_pool_stats_redis_roundtrip():
    """set/get_runner_pool_stats survive a write/read cycle."""
    stats = {"target": 2, "per_profile": {"default": {"warm": 1, "ok": True, "error": None}}, "ts": 1.0}
    await redis_core.set_runner_pool_stats(stats)
    got = await redis_core.get_runner_pool_stats()
    assert got is not None
    assert got["target"] == 2
    assert got["per_profile"]["default"]["warm"] == 1
    # missing key → None
    await redis_core.get_redis().delete("hermes:runner:pool")
    assert await redis_core.get_runner_pool_stats() is None
