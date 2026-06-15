"""Access/refresh token revocation: logout, password change, error envelope.

Requires reachable PostgreSQL (conftest) + Redis. The Redis-backed cases skip
cleanly when Redis is down so the file stays runnable without the full stack.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient


async def _redis_up() -> bool:
    from app.core.redis import get_redis
    try:
        await get_redis().ping()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def _require_redis():
    if not await _redis_up():
        pytest.skip("Redis not reachable — revocation lives in Redis")


async def _login(client: AsyncClient) -> tuple[str, str]:
    """Log the seeded test_user in; returns (access, refresh)."""
    resp = await client.post("/api/v1/auth/login", json={
        "username": "test@hermes.io", "password": "Test@1234",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["access_token"], body["refresh_token"]


@pytest.mark.asyncio
async def test_logout_revokes_access_token(client: AsyncClient, test_user, _require_redis):
    access, refresh = await _login(client)
    headers = {"Authorization": f"Bearer {access}"}

    # Valid before logout.
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    # Logout blacklists the access token (header) and refresh token (body).
    out = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh}, headers=headers)
    assert out.status_code == 204

    # The very same access token is now rejected — no waiting for exp.
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client: AsyncClient, test_user, _require_redis):
    access, refresh = await _login(client)
    headers = {"Authorization": f"Bearer {access}"}

    await client.post("/api/v1/auth/logout", json={"refresh_token": refresh}, headers=headers)

    # A logged-out refresh token can't mint new access tokens.
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert reuse.status_code == 401


@pytest.mark.asyncio
async def test_change_password_revokes_old_returns_new(client: AsyncClient, test_user, _require_redis):
    access, _ = await _login(client)
    old_headers = {"Authorization": f"Bearer {access}"}

    resp = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "Test@1234", "new_password": "Test@5678"},
        headers=old_headers,
    )
    assert resp.status_code == 200, resp.text
    new_access = resp.json()["access_token"]

    # Old token dead, freshly-issued token works.
    assert (await client.get("/api/v1/auth/me", headers=old_headers)).status_code == 401
    new_headers = {"Authorization": f"Bearer {new_access}"}
    assert (await client.get("/api/v1/auth/me", headers=new_headers)).status_code == 200


@pytest.mark.asyncio
async def test_http_error_carries_request_id(client: AsyncClient):
    """The global handler wraps even plain HTTP errors with a correlation id."""
    resp = await client.get("/api/v1/conversations/does-not-exist-route-zzz")
    assert resp.status_code in (401, 404, 422)
    assert "request_id" in resp.json()
