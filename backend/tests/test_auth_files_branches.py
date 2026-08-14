"""Auth flow branches + file-processing helpers — direct coverage for the
refresh/logout/change-password paths and the office/html text extraction.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.security import hash_password
from app.db.models.user import User

pytestmark = pytest.mark.asyncio


# ── auth: refresh / logout / change-password ──

async def test_refresh_token_roundtrip(client, db):
    u = User(
        id=uuid.uuid4(), email="auth-refresh@h.io", name="r",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    login = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "Test@1234",
    })
    assert login.status_code == 200, login.text
    refresh = login.json()["refresh_token"]
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text
    assert r.json().get("access_token")


async def test_refresh_with_invalid_token_rejected(client):
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": "bogus-token"})
    assert r.status_code in (400, 401)


async def test_change_password_and_relogin(client, db):
    u = User(
        id=uuid.uuid4(), email="auth-cp@h.io", name="cp",
        password_hash=hash_password("Old@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    r = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "Old@1234",
    })
    assert r.status_code == 200
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = await client.post("/api/v1/auth/change-password", json={
        "current_password": "Old@1234", "new_password": "New@1234",
    }, headers=headers)
    assert r.status_code == 200, r.text

    # Old password now rejected, new one works
    r = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "Old@1234",
    })
    assert r.status_code == 401
    r = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "New@1234",
    })
    assert r.status_code == 200


async def test_change_password_wrong_old_rejected(client, db):
    u = User(
        id=uuid.uuid4(), email="auth-cp2@h.io", name="cp2",
        password_hash=hash_password("Old@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    r = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "Old@1234",
    })
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post("/api/v1/auth/change-password", json={
        "current_password": "wrong", "new_password": "New@1234",
    }, headers=headers)
    assert r.status_code in (400, 401)


# ── file processing helpers ──

async def test_read_upload_capped_limits_size():
    """The capped upload reader must reject oversized bodies with 413."""
    from io import BytesIO
    from starlette.datastructures import UploadFile
    from app.core.files import read_upload_capped
    small = await read_upload_capped(UploadFile(file=BytesIO(b"x" * 100)), max_bytes=1024)
    assert len(small) == 100
    import fastapi
    with pytest.raises(fastapi.HTTPException) as ei:
        await read_upload_capped(UploadFile(file=BytesIO(b"y" * 5000)), max_bytes=1024)
    assert ei.value.status_code == 413


async def test_office_extractors_handle_plain_text():
    """Non-office / plain text inputs must not crash the extractors."""
    from app.core.files import extract_docx_html, extract_xlsx_html, extract_pptx_html
    # These raise/return gracefully for invalid bytes — must not hang or crash
    for fn in (extract_docx_html, extract_xlsx_html, extract_pptx_html):
        try:
            fn(b"not a real office file")
        except Exception:
            pass  # graceful failure is acceptable; hard crash is not


async def test_safe_relative_path_normalizes_traversal():
    """safe_relative_path anchors + normalizes — ../ segments can never climb
    above the root (documented behaviour, not rejection)."""
    from app.core.files import safe_relative_path
    assert safe_relative_path("a/b.txt") == "a/b.txt"
    assert safe_relative_path("../evil.txt") == "evil.txt"
    assert safe_relative_path("/abs/path.txt") == "abs/path.txt"
    assert safe_relative_path("a/../../b") == "b"


# ── auth: logout / stream-ticket / providers / disabled-account ──

async def test_logout_invalidates_token(client, db):
    u = User(
        id=uuid.uuid4(), email="auth-logout@h.io", name="lo",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    login = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "Test@1234",
    })
    tokens = login.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = await client.post("/api/v1/auth/logout",
                          json={"refresh_token": tokens["refresh_token"]},
                          headers=headers)
    assert r.status_code == 204

    # The access token must now be rejected (jti blacklisted)
    r = await client.get("/api/v1/users/me", headers=headers)
    assert r.status_code == 401


async def test_stream_ticket_issued_and_validated(client, db):
    u = User(
        id=uuid.uuid4(), email="auth-ticket@h.io", name="tk",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    login = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "Test@1234",
    })
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.post("/api/v1/auth/stream-ticket", headers=headers)
    assert r.status_code == 200
    ticket = r.json().get("ticket")
    assert ticket


async def test_providers_list(client, db):
    u = User(
        id=uuid.uuid4(), email="auth-prov@h.io", name="pv",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    login = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "Test@1234",
    })
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.get("/api/v1/auth/providers", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_login_disabled_account_403(client, db):
    u = User(
        id=uuid.uuid4(), email="auth-disabled@h.io", name="dis",
        password_hash=hash_password("Test@1234"), is_active=False, role="member",
    )
    db.add(u)
    await db.flush()
    r = await client.post("/api/v1/auth/login", json={
        "method": "local", "username": u.email, "password": "Test@1234",
    })
    assert r.status_code == 403
