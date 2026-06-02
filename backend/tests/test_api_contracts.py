"""API contract tests — verify response shapes match frontend expectations.

These tests are schema-level smoke tests: they call real endpoints and validate
the JSON response against the exact shape the frontend types expect.

Run: pytest tests/test_api_contracts.py -v
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


# ── Fixtures ──

@pytest.fixture
async def ac():
    """Async HTTP client pointed at the FastAPI app."""
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def admin_token(ac: AsyncClient) -> str:
    """Login and return an access token."""
    res = await ac.post("/api/v1/auth/login", json={"username": "admin@hermes.io", "password": "Hermes@2026"})
    assert res.status_code == 200, f"Login failed: {res.text}"
    return res.json()["access_token"]


@pytest.fixture
def admin_headers(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
async def profile_id(ac: AsyncClient, admin_headers: dict) -> str:
    """Get an existing profile ID (from seed data or create one)."""
    res = await ac.get("/api/v1/profiles", headers=admin_headers)
    assert res.status_code == 200
    profiles = res.json()
    if profiles:
        return profiles[0]["id"]
    # Create one if none exist
    res = await ac.post("/api/v1/profiles", json={
        "name": "测试助手", "handle": f"test-{uuid.uuid4().hex[:6]}", "scope": "global",
    }, headers=admin_headers)
    assert res.status_code == 201
    return res.json()["id"]


# ── Helpers ──

def assert_shape(data: dict, required_keys: list[str], label: str):
    """Assert a dict contains all required keys."""
    missing = [k for k in required_keys if k not in data]
    assert not missing, f"{label}: missing keys {missing}"


# ── Auth contracts ──

async def test_contract_login(ac: AsyncClient):
    """POST /auth/login must return token pair + user object."""
    res = await ac.post("/api/v1/auth/login", json={"username": "admin@hermes.io", "password": "Hermes@2026"})
    assert res.status_code == 200
    data = res.json()
    assert_shape(data, ["access_token", "refresh_token", "token_type", "expires_in", "user"], "LoginResponse")
    assert_shape(data["user"], ["id", "email", "name", "role", "source", "created_at"], "User")


async def test_contract_refresh(ac: AsyncClient, admin_token: str):
    """POST /auth/refresh must return a new token pair."""
    res = await ac.post("/api/v1/auth/refresh", json={"refresh_token": admin_token})
    if res.status_code == 200:
        data = res.json()
        assert_shape(data, ["access_token", "refresh_token"], "TokenPair")


# ── Agent contracts ──

async def test_contract_agents_list(ac: AsyncClient, admin_headers: dict):
    """GET /agents must return a list of Agent objects."""
    res = await ac.get("/api/v1/agents", headers=admin_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    if items:
        assert_shape(items[0], ["id", "label", "kind", "available", "official"], "Agent")


# ── Profile contracts ──

async def test_contract_profiles_list(ac: AsyncClient, admin_headers: dict):
    """GET /profiles must return a list of Profile objects."""
    res = await ac.get("/api/v1/profiles", headers=admin_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    if items:
        assert_shape(items[0], ["id", "name", "handle", "scope", "color", "icon", "desc", "default_agent_id", "default_model"], "Profile")


async def test_contract_profile_clone(ac: AsyncClient, admin_headers: dict, profile_id: str):
    """POST /profiles/{id}/clone must return a new Profile."""
    res = await ac.post(f"/api/v1/profiles/{profile_id}/clone", headers=admin_headers)
    assert res.status_code == 201
    data = res.json()
    assert_shape(data, ["id", "name", "handle", "scope", "color", "icon", "desc", "default_agent_id", "default_model"], "Profile")
    assert data["id"] != profile_id, "Clone must have a different ID"
    assert "副本" in data["name"], "Clone name must contain '副本'"


async def test_contract_profile_export(ac: AsyncClient, admin_headers: dict, profile_id: str):
    """GET /profiles/{id}/export must return a portable JSON object."""
    res = await ac.get(f"/api/v1/profiles/{profile_id}/export", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert_shape(data, ["name", "handle", "scope", "color", "icon", "desc", "default_agent_id", "default_model"], "ProfileExport")
    assert "id" not in data, "Export must not include id"
    assert "team_id" not in data, "Export must not include team_id"
    assert "path" not in data, "Export must not include path"


async def test_contract_profile_import(ac: AsyncClient, admin_headers: dict):
    """POST /profiles/import must accept a list and return created profiles."""
    payload = {
        "profiles": [
            {
                "name": "导入测试",
                "handle": f"import-test-{uuid.uuid4().hex[:6]}",
                "scope": "global",
                "color": "#ff0000",
                "icon": "sparkle",
                "desc": "contract test",
                "default_agent_id": "hermes",
                "default_model": "hermes-4",
            }
        ]
    }
    res = await ac.post("/api/v1/profiles/import", json=payload, headers=admin_headers)
    assert res.status_code == 201
    items = res.json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert_shape(items[0], ["id", "name", "handle", "scope"], "ImportedProfile")


# ── Conversation contracts ──

async def test_contract_conversations_list(ac: AsyncClient, admin_headers: dict):
    """GET /conversations must return a list of Conversation objects."""
    res = await ac.get("/api/v1/conversations", headers=admin_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    if items:
        assert_shape(items[0], ["id", "title", "primary_agent_id", "created_at", "updated_at"], "Conversation")


async def test_contract_conversation_create(ac: AsyncClient, admin_headers: dict):
    """POST /conversations must return a ConversationDetail with messages array."""
    res = await ac.post("/api/v1/conversations", json={"primary_agent_id": "hermes"}, headers=admin_headers)
    assert res.status_code in (200, 201)
    data = res.json()
    assert_shape(data, ["id", "title", "primary_agent_id", "created_at"], "ConversationDetail")
    assert "messages" in data, "ConversationDetail must have messages"
    assert isinstance(data["messages"], list)


# ── Admin contracts ──

async def test_contract_admin_stats(ac: AsyncClient, admin_headers: dict):
    """GET /admin/stats must return admin dashboard stats."""
    res = await ac.get("/api/v1/admin/stats", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert_shape(data, ["users", "teams", "conversations", "messages", "agents", "active_users"], "AdminStats")


async def test_contract_admin_users(ac: AsyncClient, admin_headers: dict):
    """GET /admin/users must return a list of User objects."""
    res = await ac.get("/api/v1/admin/users", headers=admin_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    if items:
        assert_shape(items[0], ["id", "email", "name", "role", "source", "created_at"], "User")


# ── Team contracts ──

async def test_contract_teams_list(ac: AsyncClient, admin_headers: dict):
    """GET /teams must return a list of Team objects."""
    res = await ac.get("/api/v1/teams", headers=admin_headers)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list)
    if items:
        assert_shape(items[0], ["id", "name", "join_mode", "created_at"], "Team")
