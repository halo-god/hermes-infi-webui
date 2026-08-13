"""API contract tests — verify response shapes match frontend expectations.

These tests are schema-level smoke tests: they call real endpoints and validate
the JSON response against the exact shape the frontend types expect.

Run: pytest tests/test_api_contracts.py -v
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
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
    # list_profiles falls back to a HARDCODED profile (handle "hermes-main",
    # freshly generated uuid) when the DB is empty — that id is not a real
    # row, so PK lookups (clone/export) 404. Treat it as "none exist".
    if profiles and profiles[0].get("handle") != "hermes-main":
        return profiles[0]["id"]
    # Create one if none exist
    res = await ac.post("/api/v1/profiles", json={
        "name": "测试助手", "handle": f"test-{uuid.uuid4().hex[:6]}", "scope": "global",
    }, headers=admin_headers)
    assert res.status_code == 201
    return res.json()["id"]


@pytest_asyncio.fixture(autouse=True, scope="module")
async def _cleanup_created_profiles():
    """These contract tests commit through the API (no rollback txn), so every
    created profile would linger in the shared hermes_test DB. Leftovers with
    real ~/.hermes paths then make ingest-style tests scan production skill
    dirs. Snapshot before, delete everything new after the module."""
    from sqlalchemy import text as _sa_text

    async def _ids() -> set[str]:
        from app.db.base import async_session_maker
        async with async_session_maker() as s:
            rows = (await s.execute(_sa_text("SELECT id::text FROM profiles"))).scalars().all()
            return set(rows)

    before = await _ids()
    yield
    after = await _ids()
    leaked = after - before
    if leaked:
        from app.db.base import async_session_maker
        async with async_session_maker() as s:
            await s.execute(_sa_text(
                "DELETE FROM profiles WHERE id::text = ANY(:ids)"
            ).bindparams(ids=list(leaked)))
            await s.commit()


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


# ── Authorization gates (P0/P1 review fixes) ──

async def _mk_api_user(email: str, role: str = "member"):
    """Create a user row visible to the API.

    Uses its own session + commit — the `db` fixture wraps everything in an
    outer transaction (rollback after test), so rows added through it are
    invisible to the API's separate connections.
    """
    from app.core.security import hash_password
    from app.db.base import async_session_maker
    from app.db.models.user import User
    async with async_session_maker() as s:
        u = User(
            id=uuid.uuid4(), email=email, name=email.split("@")[0],
            password_hash=hash_password("Test@1234"), is_active=True, role=role,
        )
        s.add(u)
        await s.commit()
        return u


async def _login(ac: AsyncClient, email: str) -> dict:
    res = await ac.post("/api/v1/auth/login", json={
        "username": email, "password": "Test@1234",
    })
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def test_contract_profiles_list_strips_system_prompt_for_member(ac: AsyncClient, db):
    """Non-admins must NOT see system_prompt in the profile list — it carries
    the assistant persona + knowledge bindings (manager-only)."""
    u = await _mk_api_user("member-strip@h.io")
    h = await _login(ac, u.email)
    res = await ac.get("/api/v1/profiles", headers=h)
    assert res.status_code == 200
    for p in res.json():
        assert p.get("system_prompt") is None, f"system_prompt leaked: {p.get('handle')}"


async def test_contract_group_add_member_requires_admin(ac: AsyncClient, db):
    """A plain group member cannot add other users — that would drag outsiders
    into the group where get_conversation exposes the full history."""
    from app.db.models.conversation import GroupMember
    from app.services import conversation_service as svc
    from app.db.base import async_session_maker

    owner = await _mk_api_user("gm-owner@h.io")
    member = await _mk_api_user("gm-member@h.io")
    outsider = await _mk_api_user("gm-outsider@h.io")

    async with async_session_maker() as s:
        group = await svc.create_group(
            s, owner.id, title="权限测试群", member_user_ids=[member.id],
        )
        await s.commit()
        assert await svc.is_group_admin(s, group.id, owner.id)

    h = await _login(ac, member.email)
    res = await ac.post(
        f"/api/v1/conversations/{group.id}/members",
        json={"user_id": str(outsider.id)},
        headers=h,
    )
    assert res.status_code == 403, res.text

    # The owner (admin) can add the outsider.
    h_admin = await _login(ac, owner.email)
    res = await ac.post(
        f"/api/v1/conversations/{group.id}/members",
        json={"user_id": str(outsider.id)},
        headers=h_admin,
    )
    assert res.status_code == 201, res.text
    from sqlalchemy import select
    db.expire_all()
    rows = (await db.execute(
        select(GroupMember).where(
            GroupMember.conversation_id == group.id,
        )
    )).scalars().all()
    assert any(m.user_id == outsider.id for m in rows)
