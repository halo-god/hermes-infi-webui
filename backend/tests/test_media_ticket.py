"""Media tickets: minting + that stream/raw endpoints accept a ticket (URL)
instead of the API access token.

Requires PostgreSQL (conftest) + Redis; Redis-backed cases skip if Redis down.
"""
from __future__ import annotations

import uuid

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
        pytest.skip("Redis not reachable — media tickets live in Redis")


@pytest.mark.asyncio
async def test_mint_ticket_requires_auth(client: AsyncClient):
    assert (await client.post("/api/v1/auth/stream-ticket")).status_code == 401


@pytest.mark.asyncio
async def test_mint_and_resolve_ticket(client: AsyncClient, auth_headers, test_user, _require_redis):
    resp = await client.post("/api/v1/auth/stream-ticket", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ticket"] and body["expires_in"] > 0

    from app.core.redis import resolve_media_ticket
    assert await resolve_media_ticket(body["ticket"]) == str(test_user.id)
    assert await resolve_media_ticket("not-a-real-ticket") is None


@pytest.mark.asyncio
async def test_file_raw_rejects_bad_ticket(client: AsyncClient, _require_redis):
    # Auth runs before the file lookup, so a bogus ticket is a clean 401.
    url = f"/api/v1/conversations/{uuid.uuid4()}/files/{uuid.uuid4()}/raw?ticket=bogus"
    assert (await client.get(url)).status_code == 401


@pytest.mark.asyncio
async def test_file_raw_accepts_valid_ticket(client: AsyncClient, auth_headers, test_user, _require_redis):
    mint = await client.post("/api/v1/auth/stream-ticket", headers=auth_headers)
    ticket = mint.json()["ticket"]
    # Valid ticket → auth passes; the missing conversation then yields 404 (not 401).
    url = f"/api/v1/conversations/{uuid.uuid4()}/files/{uuid.uuid4()}/raw?ticket={ticket}"
    assert (await client.get(url)).status_code == 404


@pytest.mark.asyncio
async def test_sse_stream_rejects_raw_access_token(client: AsyncClient, user_token, _require_redis):
    # The old escape hatch (access token in the URL) must be gone: passing it as
    # a ticket fails because it isn't a minted media ticket.
    url = f"/api/v1/conversations/{uuid.uuid4()}/stream?ticket={user_token}"
    assert (await client.get(url)).status_code == 401
