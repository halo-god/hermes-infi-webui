"""End-to-end auth flow integration test.

Requires reachable PostgreSQL + Redis (DATABASE_URL / REDIS_URL).
Run after `alembic upgrade head` && `python -m app.seed`:

    DATABASE_URL=postgresql+asyncpg://hermes:hermes@localhost:5432/hermes \
    REDIS_URL=redis://localhost:6379/0 \
    SECRET_KEY=test-secret \
    pytest tests/test_auth_flow.py

Uses a single event loop (httpx ASGITransport) — the deprecated Starlette
TestClient spins a fresh loop per request and breaks the long-lived asyncpg
pool, which is a test-harness artifact, not an app bug.
"""
from __future__ import annotations


import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import settings
from app.db.base import async_session_maker
from app.main import app

PREFIX = settings.api_v1_prefix
ADMIN_EMAIL = settings.first_admin_email
ADMIN_PW = settings.first_admin_password


async def _db_reachable() -> bool:
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_full_auth_flow():
    if not await _db_reachable():
        pytest.skip("PostgreSQL not reachable — set DATABASE_URL and run migrations+seed")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # health
        assert (await c.get(f"{PREFIX}/healthz")).status_code == 200

        # providers — only local enabled
        provs = (await c.get(f"{PREFIX}/auth/providers")).json()
        assert {p["id"]: p["enabled"] for p in provs}["local"] is True

        # wrong password
        bad = await c.post(
            f"{PREFIX}/auth/login",
            json={"method": "local", "username": ADMIN_EMAIL, "password": "nope"},
        )
        assert bad.status_code == 401

        # correct login
        ok = await c.post(
            f"{PREFIX}/auth/login",
            json={"method": "local", "username": ADMIN_EMAIL, "password": ADMIN_PW},
        )
        assert ok.status_code == 200, ok.text
        data = ok.json()
        assert data["user"]["role"] == "super_admin"
        access, refresh = data["access_token"], data["refresh_token"]

        # me: unauth → 401, auth → 200
        assert (await c.get(f"{PREFIX}/auth/me")).status_code == 401
        me = await c.get(f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me.status_code == 200 and me.json()["email"] == ADMIN_EMAIL

        # admin-only listing (RBAC) — user management lives under /admin
        ulist = await c.get(f"{PREFIX}/admin/users", headers={"Authorization": f"Bearer {access}"})
        assert ulist.status_code == 200

        # refresh rotation + reuse blacklist
        rf = await c.post(f"{PREFIX}/auth/refresh", json={"refresh_token": refresh})
        assert rf.status_code == 200 and rf.json()["access_token"] != access
        reuse = await c.post(f"{PREFIX}/auth/refresh", json={"refresh_token": refresh})
        assert reuse.status_code == 401  # consumed refresh is revoked

        # external provider login is always blocked without valid creds/config
        # (403 disabled · 401 bad creds · 503 unreachable) — never a session
        ldap = await c.post(
            f"{PREFIX}/auth/login", json={"method": "ldap", "username": "x", "password": "y"}
        )
        assert ldap.status_code >= 400
