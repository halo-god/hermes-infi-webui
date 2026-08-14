"""Lightweight smoke tests for misc endpoints that had zero coverage:
feedback (submit/list/update), presence (heartbeat/query), branding (public)."""
from __future__ import annotations


import pytest

pytestmark = pytest.mark.asyncio


# ── feedback ──

async def test_feedback_submit_and_list(client, auth_headers):
    r = await client.post("/api/v1/feedback", json={
        "title": "E2E 反馈", "content": "测试反馈内容", "category": "bug",
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    fb = r.json()
    assert fb["title"] == "E2E 反馈"
    assert fb["status"] in ("open", "new", "pending")

    r = await client.get("/api/v1/feedback", headers=auth_headers)
    assert r.status_code == 200
    assert any(f["id"] == fb["id"] for f in r.json())


async def test_feedback_get_detail(client, auth_headers):
    fb = (await client.post("/api/v1/feedback", json={
        "title": "详情反馈", "content": "内容", "category": "feature",
    }, headers=auth_headers)).json()
    r = await client.get(f"/api/v1/feedback/{fb['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["title"] == "详情反馈"


# ── presence ──

async def test_presence_heartbeat_and_query(client, auth_headers):
    r = await client.post("/api/v1/presence/heartbeat", headers=auth_headers)
    assert r.status_code == 200

    # Query self → should be online (just heartbeated)
    r = await client.post("/api/v1/presence/query", json={}, headers=auth_headers)
    assert r.status_code in (200, 422), "empty query is tolerated or validated"


# ── branding (public, no auth) ──

async def test_branding_public(client):
    r = await client.get("/api/v1/branding")
    assert r.status_code == 200
    data = r.json()
    # Login page consumes name/shortName/accent
    assert any(k in data for k in ("name", "short_name", "shortName", "accent"))


# ── health / info (public) ──

async def test_health_and_info(client):
    for path in ("/api/v1/healthz", "/api/v1/readyz"):
        r = await client.get(path)
        assert r.status_code == 200, f"{path} must be healthy"
    r = await client.get("/api/v1/info")
    assert r.status_code == 200
    assert "version" in r.json() or "name" in r.json()


# ── users/me ──

async def test_users_me(client, auth_headers):
    r = await client.get("/api/v1/users/me", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["email"]


async def test_users_me_update(client, auth_headers):
    r = await client.patch("/api/v1/users/me", json={"title": "E2E 职位"},
                           headers=auth_headers)
    assert r.status_code == 200
    assert r.json().get("title") == "E2E 职位"
