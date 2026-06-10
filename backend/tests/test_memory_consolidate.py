"""做梦整理记忆 (memory consolidation) tests.

Pure helpers (parse/trim/excerpt) need nothing; the PUT /memory budget tests
need only the DB; the consolidate-trigger tests need reachable Redis
(skipped otherwise, same convention as test_clarify_v2.py).
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.config import settings
from app.core import redis as R
from app.core.security import create_token
from app.db.models.user import User
from agent_runner.runner import _message_excerpt, parse_memory_json, trim_memory_to_budget


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis():
    """The cached redis client binds to the creating event loop; each test
    runs in a new loop (asyncio_mode=auto), so recreate it per test.
    Closing a client owned by another file's loop can itself raise — ignore."""
    try:
        await R.close_redis()
    except Exception:
        R._client = None
    yield
    try:
        await R.close_redis()
    except Exception:
        R._client = None


async def _redis_ok() -> bool:
    try:
        await R.get_redis().ping()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def super_admin_user(db) -> User:
    from app.core.security import hash_password
    user = User(
        id=uuid.uuid4(),
        email="root@hermes.io",
        name="Root",
        password_hash=hash_password("Root@1234"),
        is_active=True,
        role="super_admin",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest.fixture
def super_admin_headers(super_admin_user: User) -> dict[str, str]:
    token, _ = create_token(str(super_admin_user.id), "access")
    return {"Authorization": f"Bearer {token}"}


async def _cleanup_keys(*user_ids: uuid.UUID) -> None:
    r = R.get_redis()
    for uid in user_ids:
        await r.delete(R.mem_consolidate_status_key(str(uid)))
        await r.delete(R.mem_consolidate_cooldown_key(str(uid)))


# ── parse_memory_json ──

def test_parse_clean_json():
    out = parse_memory_json('{"user_profile": "工程师", "soul": "顾问", "notes": "测试"}')
    assert out == {"user_profile": "工程师", "soul": "顾问", "notes": "测试"}


def test_parse_fenced_json():
    text = '好的，整理结果如下：\n```json\n{"user_profile": "P", "soul": "S", "notes": "N"}\n```\n'
    assert parse_memory_json(text) == {"user_profile": "P", "soul": "S", "notes": "N"}


def test_parse_json_with_surrounding_prose():
    text = '以下是更新后的记忆 {"user_profile": "P", "soul": "", "notes": ""} 希望有帮助'
    out = parse_memory_json(text)
    assert out is not None
    assert out["user_profile"] == "P"
    assert out["soul"] == ""


def test_parse_garbage_returns_none():
    assert parse_memory_json("这不是 JSON") is None
    assert parse_memory_json("") is None
    assert parse_memory_json("[1, 2, 3]") is None
    # dict without any of the three keys → nothing usable
    assert parse_memory_json('{"foo": "bar"}') is None


# ── trim_memory_to_budget ──

def test_trim_under_budget_unchanged():
    mem = {"user_profile": "a" * 100, "soul": "b" * 100, "notes": "c" * 100}
    assert trim_memory_to_budget(mem, 2200) == mem


def test_trim_over_budget():
    mem = {"user_profile": "a" * 2000, "soul": "b" * 2000, "notes": "c" * 2000}
    out = trim_memory_to_budget(mem, 2200)
    assert sum(len(v) for v in out.values()) <= 2200
    # Proportional: every field keeps some content
    assert all(out.values())


# ── _message_excerpt ──

def _msg(role: str, content: dict, status: str = "complete"):
    return SimpleNamespace(role=role, content=content, status=status)


def test_excerpt_skips_system_and_error():
    assert _message_excerpt(_msg("system", {"text": "x"})) is None
    assert _message_excerpt(_msg("agent", {"text": "x"}, status="error")) is None


def test_excerpt_user_agent_roundtable():
    assert _message_excerpt(_msg("user", {"text": "你好"})) == "用户: 你好"
    assert _message_excerpt(_msg("agent", {"text": "答复"})) == "AI: 答复"
    assert _message_excerpt(
        _msg("roundtable", {"merged": {"text": "综合结论"}})
    ) == "AI(圆桌): 综合结论"
    assert _message_excerpt(_msg("agent", {"text": ""})) is None


def test_excerpt_truncates_long_text():
    long = "x" * (settings.memory_consolidate_msg_chars + 100)
    out = _message_excerpt(_msg("user", {"text": long}))
    assert out is not None
    assert len(out) <= settings.memory_consolidate_msg_chars + len("用户: ") + 1  # +1 for "…"


# ── PUT /memory budget validation ──

async def test_put_memory_within_budget(client, auth_headers):
    resp = await client.put(
        "/api/v1/memory",
        json={"notes": "n" * 700, "user_profile": "p" * 700, "soul": "s" * 700},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["notes"] == "n" * 700
    assert "last_consolidated_at" in data


async def test_put_memory_over_budget(client, auth_headers):
    resp = await client.put(
        "/api/v1/memory",
        json={"notes": "n" * 2300},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "超出上限" in resp.json()["detail"]


async def test_put_memory_merged_total_over_budget(client, auth_headers):
    # Saved fields count toward the budget even when the new payload omits them.
    resp = await client.put("/api/v1/memory", json={"notes": "n" * 1200}, headers=auth_headers)
    assert resp.status_code == 200
    resp = await client.put(
        "/api/v1/memory", json={"user_profile": "p" * 1100}, headers=auth_headers
    )
    assert resp.status_code == 422


# ── POST /memory/consolidate + status (needs Redis) ──

async def test_consolidate_cooldown_for_member(client, auth_headers, test_user):
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    try:
        resp = await client.post("/api/v1/memory/consolidate", headers=auth_headers)
        assert resp.status_code == 202

        # Cooldown armed → immediate retry rejected
        resp = await client.post("/api/v1/memory/consolidate", headers=auth_headers)
        assert resp.status_code == 429

        st = await client.get("/api/v1/memory/consolidate/status", headers=auth_headers)
        assert st.status_code == 200
        body = st.json()
        assert body["status"] == "running"
        assert body["cooldown_remaining"] > 0
    finally:
        await _cleanup_keys(test_user.id)


async def test_consolidate_super_admin_bypasses_cooldown(
    client, super_admin_headers, super_admin_user
):
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    try:
        resp = await client.post("/api/v1/memory/consolidate", headers=super_admin_headers)
        assert resp.status_code == 202
        # Running lock still applies even for super_admin
        resp = await client.post("/api/v1/memory/consolidate", headers=super_admin_headers)
        assert resp.status_code == 409

        # After the run finishes (simulate runner writing done), retrigger works at once
        await R.get_redis().set(
            R.mem_consolidate_status_key(str(super_admin_user.id)),
            json.dumps({"status": "done"}),
            ex=60,
        )
        resp = await client.post("/api/v1/memory/consolidate", headers=super_admin_headers)
        assert resp.status_code == 202

        st = await client.get("/api/v1/memory/consolidate/status", headers=super_admin_headers)
        assert st.json()["cooldown_remaining"] == 0
    finally:
        await _cleanup_keys(super_admin_user.id)


async def test_consolidate_status_idle_by_default(client, auth_headers):
    if not await _redis_ok():
        pytest.skip("Redis not reachable")
    st = await client.get("/api/v1/memory/consolidate/status", headers=auth_headers)
    assert st.status_code == 200
    assert st.json()["status"] == "idle"
