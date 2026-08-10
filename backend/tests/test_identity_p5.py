"""P5: LDAP login + dept→team mapping, agent sandbox, metrics.

LDAP is exercised against an in-memory ldap3 MOCK_SYNC directory (no server),
proving: enable provider → configure dept→team mapping → LDAP user logs in →
gets provisioned, dept-mapped role, and auto-joined to the team.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import settings
from app.db.base import async_session_maker
from app.main import app
from app.services import identity_service

PREFIX = settings.api_v1_prefix

# mock LDAP directory
LDAP_USER_DN = "uid=zhiwei,ou=people,dc=hermes,dc=io"
LDAP_PASSWORD = "Secret123!"


def _mock_factory(config, user_dn, password):
    """Build a MOCK_SYNC connection with one seeded user; bind validates pw."""
    from ldap3 import Connection, MOCK_SYNC, Server

    server = Server("mock_ldap")
    conn = Connection(server, user=user_dn, password=password, client_strategy=MOCK_SYNC)
    conn.strategy.add_entry(
        LDAP_USER_DN,
        {
            "objectClass": ["inetOrgPerson"],
            "userPassword": LDAP_PASSWORD,
            "mail": "zhiwei@hermes.io",
            "cn": "林知微",
            "departmentNumber": "Design",
        },
    )
    return conn


async def _ok() -> bool:
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _ensure_provider(pid: str, label: str) -> None:
    """The test DB is recreated per session (drop schema) and providers are
    seed-only — PATCH /admin/identity/{pid} 404s without the row. Insert it."""
    from app.db.models.identity import IdentityProvider

    async with async_session_maker() as db:
        p = await db.get(IdentityProvider, pid)
        if p is None:
            db.add(IdentityProvider(id=pid, label=label, enabled=False, config={}))
            await db.commit()


@pytest.mark.asyncio
async def test_ldap_login_and_mapping():
    if not await _ok():
        pytest.skip("PostgreSQL not reachable")

    await _ensure_provider("ldap", "LDAP")

    # inject the mock LDAP connection factory
    identity_service.LDAP._factory = _mock_factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        admin_h = {
            "Authorization": "Bearer "
            + (
                await c.post(
                    f"{PREFIX}/auth/login",
                    json={"method": "local", "username": settings.first_admin_email,
                          "password": settings.first_admin_password},
                )
            ).json()["access_token"]
        }

        # a team to auto-join
        tid = (await c.post(f"{PREFIX}/teams", json={"name": f"设计组-{uuid.uuid4().hex[:6]}"}, headers=admin_h)).json()["id"]

        # LDAP disabled → login blocked
        r = await c.post(f"{PREFIX}/auth/login", json={"method": "ldap", "username": "zhiwei", "password": LDAP_PASSWORD})
        assert r.status_code == 403

        # start from a clean mapping set (the dev DB persists across runs)
        for m in (await c.get(f"{PREFIX}/admin/identity/ldap/mappings", headers=admin_h)).json():
            await c.delete(f"{PREFIX}/admin/identity/mappings/{m['id']}", headers=admin_h)

        # enable + configure LDAP
        r = await c.patch(
            f"{PREFIX}/admin/identity/ldap",
            json={"enabled": True, "config": {
                "host": "ldap.hermes.io", "port": 389,
                "user_dn_template": "uid={username},ou=people,dc=hermes,dc=io",
                "attr_email": "mail", "attr_name": "cn", "attr_dept": "departmentNumber",
            }},
            headers=admin_h,
        )
        assert r.status_code == 200 and r.json()["enabled"] is True

        # dept→team mapping: Design → team (as team_admin)
        r = await c.post(
            f"{PREFIX}/admin/identity/ldap/mappings",
            json={"match_basis": "attribute", "source_value": "Design",
                  "dept": "设计", "default_role": "team_admin", "auto_join_team_id": tid},
            headers=admin_h,
        )
        assert r.status_code == 201

        # providers endpoint reflects enablement
        provs = (await c.get(f"{PREFIX}/auth/providers")).json()
        assert any(p["id"] == "ldap" and p["enabled"] for p in provs)

        # wrong LDAP password → 401
        bad = await c.post(f"{PREFIX}/auth/login", json={"method": "ldap", "username": "zhiwei", "password": "nope"})
        assert bad.status_code == 401

        # correct LDAP login → provisioned + dept-mapped
        r = await c.post(f"{PREFIX}/auth/login", json={"method": "ldap", "username": "zhiwei", "password": LDAP_PASSWORD})
        assert r.status_code == 200, r.text
        u = r.json()["user"]
        assert u["source"] == "ldap" and u["email"] == "zhiwei@hermes.io"
        ldap_token = r.json()["access_token"]

        # auto-joined the mapped team as team_admin
        members = (await c.get(f"{PREFIX}/teams/{tid}/members", headers=admin_h)).json()
        me = next((m for m in members if m["email"] == "zhiwei@hermes.io"), None)
        assert me is not None and me["role"] == "team_admin"

        # second login reuses the same account (no duplicate)
        r2 = await c.post(f"{PREFIX}/auth/login", json={"method": "ldap", "username": "zhiwei", "password": LDAP_PASSWORD})
        assert r2.json()["user"]["id"] == u["id"]

        # the LDAP user can use the app
        assert (await c.get(f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {ldap_token}"})).status_code == 200

        # cleanup: disable provider + remove mappings so other tests see defaults
        for m in (await c.get(f"{PREFIX}/admin/identity/ldap/mappings", headers=admin_h)).json():
            await c.delete(f"{PREFIX}/admin/identity/mappings/{m['id']}", headers=admin_h)
        await c.patch(f"{PREFIX}/admin/identity/ldap", json={"enabled": False}, headers=admin_h)

    identity_service.LDAP._factory = None  # reset


@pytest.mark.asyncio
async def test_sandbox_spawn_works():
    """ACP subprocess still runs correctly under rlimit sandboxing."""
    import os
    from agent_runner.acp_client import ACPClient

    settings.sandbox_enabled = True
    settings.sandbox_cpu_seconds = 60
    settings.sandbox_fsize_mb = 64
    settings.sandbox_memory_mb = 0  # avoid exec-after-fork failures
    try:
        mock = os.path.abspath(os.path.join("agent_runner", "mock_agent.py"))
        import sys

        got = {"text": ""}

        async def on_update(u):
            if u.get("sessionUpdate") == "agent_message_chunk":
                got["text"] += u["content"]["text"]

        c = ACPClient([sys.executable, mock, "--persona", "hermes", "--no-file"],
                      cwd="/tmp", on_update=on_update, on_fs_write=None)
        await c.start()
        await c.initialize()
        await c.new_session("/tmp")
        stop = await c.prompt("沙箱内能跑吗")
        await c.stop()
        assert stop == "end_turn" and got["text"].strip()
    finally:
        settings.sandbox_enabled = False


@pytest.mark.asyncio
async def test_metrics_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get(f"{PREFIX}/healthz")
        r = await c.get("/metrics")
        assert r.status_code == 200
        body = r.text
        assert "hermes_http_requests_total" in body
        assert "hermes_logins_total" in body
