"""Public branding endpoint + admin asset upload/delete.

Covers:
- GET /branding is reachable without auth and returns the default branding.
- Editing system_settings.branding propagates to the public payload.
- Admin favicon upload round-trips (upload → public URL serves bytes → delete).
- Non-admin is blocked from asset upload/delete (403).
- Invalid asset kind / mime is rejected (400).
"""
from __future__ import annotations

import io
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import settings
from app.core.redis import get_redis
from app.db.base import async_session_maker
from app.main import app
from app.schemas.user import UserCreate
from app.services import user_service

PREFIX = settings.api_v1_prefix


async def _ok() -> bool:
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        await get_redis().ping()
        return True
    except Exception:
        return False


async def _login(c, email, pw):
    r = await c.post(
        f"{PREFIX}/auth/login",
        json={"method": "local", "username": email, "password": pw},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_branding_public_and_assets():
    if not await _ok():
        pytest.skip("PostgreSQL/Redis not reachable")

    member_email = f"b-{uuid.uuid4().hex[:8]}@hermes.io"
    async with async_session_maker() as db:
        await user_service.create_user(
            db,
            UserCreate(email=member_email, name="路人", password="Member@2026", role="member"),
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        admin_h = await _login(c, settings.first_admin_email, settings.first_admin_password)
        member_h = await _login(c, member_email, "Member@2026")

        # 1. Public branding is reachable unauthenticated and well-shaped.
        r = await c.get(f"{PREFIX}/branding")
        assert r.status_code == 200, r.text
        pub = r.json()
        for k in ("tenant_name", "display", "short_name", "login_tagline", "accent"):
            assert k in pub and isinstance(pub[k], str) and pub[k]
        assert "favicon_url" in pub and "logo_url" in pub

        # 2. Editing branding text propagates to the public payload.
        s = (await c.get(f"{PREFIX}/admin/settings", headers=admin_h)).json()
        original = s["data"]["branding"]["short_name"]
        s["data"]["branding"]["short_name"] = "Acme"
        s["data"]["branding"]["login_tagline"] = "hello world"
        r = await c.put(f"{PREFIX}/admin/settings", json={"data": s["data"]}, headers=admin_h)
        assert r.status_code == 200
        pub2 = (await c.get(f"{PREFIX}/branding")).json()
        assert pub2["short_name"] == "Acme"
        assert pub2["login_tagline"] == "hello world"
        # restore
        s["data"]["branding"]["short_name"] = original
        await c.put(f"{PREFIX}/admin/settings", json={"data": s["data"]}, headers=admin_h)

        # 3. Non-admin cannot upload / delete assets.
        png = io.BytesIO(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        r = await c.post(
            f"{PREFIX}/admin/settings/asset",
            files={"file": ("f.png", png, "image/png")},
            data={"kind": "favicon"},
            headers=member_h,
        )
        assert r.status_code == 403

        # 4. Invalid kind rejected.
        png.seek(0)
        r = await c.post(
            f"{PREFIX}/admin/settings/asset",
            files={"file": ("f.png", png, "image/png")},
            data={"kind": "banner"},
            headers=admin_h,
        )
        assert r.status_code == 400

        # 5. Invalid mime rejected.
        png.seek(0)
        r = await c.post(
            f"{PREFIX}/admin/settings/asset",
            files={"file": ("f.exe", png, "application/octet-stream")},
            data={"kind": "favicon"},
            headers=admin_h,
        )
        assert r.status_code == 400

        # 6. Admin upload round-trips; public branding now exposes a favicon URL.
        png.seek(0)
        r = await c.post(
            f"{PREFIX}/admin/settings/asset",
            files={"file": ("f.png", png, "image/png")},
            data={"kind": "favicon"},
            headers=admin_h,
        )
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "favicon"
        pub3 = (await c.get(f"{PREFIX}/branding")).json()
        assert pub3["favicon_url"] and pub3["favicon_url"].startswith("/api/v1/branding/asset/favicon")

        # 7. The raw asset endpoint serves the bytes (no auth needed).
        r = await c.get(pub3["favicon_url"])
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers.get("x-content-type-options") == "nosniff"

        # 8. 404 for an asset kind that was never uploaded.
        assert (await c.get(f"{PREFIX}/branding/asset/logo")).status_code == 404

        # 9. ETag → 304 on re-request.
        etag = r.headers["etag"]
        r2 = await c.get(pub3["favicon_url"], headers={"if-none-match": etag})
        assert r2.status_code == 304

        # 10. Delete (admin) → public URL goes back to null.
        r = await c.delete(f"{PREFIX}/admin/settings/asset/favicon", headers=admin_h)
        assert r.status_code == 200 and r.json()["removed"] is True
        pub4 = (await c.get(f"{PREFIX}/branding")).json()
        assert pub4["favicon_url"] is None

        # 11. Deleting a missing asset is a no-op (removed=False), not an error.
        r = await c.delete(f"{PREFIX}/admin/settings/asset/favicon", headers=admin_h)
        assert r.status_code == 200 and r.json()["removed"] is False
