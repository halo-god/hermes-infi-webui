"""Admin users API — list/create/update/role assignment.

admin.py has ~25 endpoints but test_admin.py only covered stats; the
user-management surface (list/create/update/role) had no direct tests.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


PREFIX = "/api/v1/admin/users"


async def test_list_users(client, admin_headers):
    r = await client.get(PREFIX, headers=admin_headers)
    assert r.status_code == 200
    users = r.json()
    assert isinstance(users, list)
    assert any(u["email"] == "admin@hermes.io" for u in users)
    # Required shape for the frontend table
    assert {"id", "email", "name", "role", "created_at"} <= set(users[0].keys())


async def test_create_user(client, admin_headers):
    email = f"admin-new-{uuid.uuid4().hex[:6]}@h.io"
    r = await client.post(PREFIX, json={
        "email": email, "name": "新用户", "password": "Test@1234", "role": "member",
    }, headers=admin_headers)
    assert r.status_code == 201, r.text
    assert r.json()["email"] == email
    assert r.json()["role"] == "member"


async def test_create_user_duplicate_email_409(client, admin_headers):
    r = await client.post(PREFIX, json={
        "email": "admin@hermes.io", "name": "重复", "password": "Test@1234",
    }, headers=admin_headers)
    assert r.status_code == 409


async def test_update_user_role_and_title(client, admin_headers):
    email = f"admin-upd-{uuid.uuid4().hex[:6]}@h.io"
    created = (await client.post(PREFIX, json={
        "email": email, "name": "原名字", "password": "Test@1234",
    }, headers=admin_headers)).json()
    r = await client.patch(f"{PREFIX}/{created['id']}", json={
        "role": "admin", "title": "新职位",
    }, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    assert r.json().get("title") == "新职位"


async def test_create_user_requires_admin(client, auth_headers):
    r = await client.post(PREFIX, json={
        "email": "x@h.io", "name": "x", "password": "Test@1234",
    }, headers=auth_headers)
    assert r.status_code == 403


async def test_admin_stats_shape(client, admin_headers):
    r = await client.get("/api/v1/admin/stats", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert {"users", "teams", "conversations", "messages", "agents"} <= set(data.keys())
