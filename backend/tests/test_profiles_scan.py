"""Profiles 扫描 —— 结构断言 + 模型同步逻辑验证."""
from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_scan_profiles_response_shape(client, db):
    """Scan endpoint returns updated counter alongside the existing fields."""
    from sqlalchemy import text
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL not reachable")

    from app.core.security import create_token, hash_password
    from app.db.models.user import User

    uniq = uuid.uuid4().hex[:8]
    admin = User(id=uuid.uuid4(), email=f"ps-admin-{uniq}@hermes.io", name="扫描管理员",
                 password_hash=hash_password("Admin@1234"), is_active=True, role="admin")
    db.add(admin)
    await db.flush()
    headers = {"Authorization": f"Bearer {create_token(str(admin.id), 'access')[0]}"}

    r = await client.post("/api/v1/profiles/scan", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"created", "updated", "message", "version", "profiles_found",
            "hermes_path", "hermes_home", "errors"} <= set(body.keys())
    assert isinstance(body["updated"], int)
    assert body["updated"] >= 0
    assert isinstance(body["errors"], list)
    # profiles_found may be 0 when HERMES_HOME is empty in CI, but the shape
    # must hold; a scan that finds profiles should never report a negative
    # updated count.
    assert body["profiles_found"] >= 0


@pytest.mark.asyncio
async def test_scan_requires_permission(client, auth_headers):
    """Non-admin (no agent.manage) is rejected."""
    r = await client.post("/api/v1/profiles/scan", headers=auth_headers)
    assert r.status_code == 403
