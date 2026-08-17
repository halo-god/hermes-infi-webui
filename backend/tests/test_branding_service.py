"""Branding service tests — public payload merge + asset lifecycle."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import branding_service
from app.services.branding_service import (
    ALLOWED_ASSET_MIMES,
    _normalize_kind,
    _asset_url,
)


def test_normalize_kind_lowercases_and_strips():
    assert _normalize_kind(" Favicon ") == "favicon"
    assert _normalize_kind("LOGO") == "logo"


def test_normalize_kind_rejects_unknown():
    with pytest.raises(ValueError):
        _normalize_kind("banner")


def test_asset_url_cache_busts_with_timestamp():
    dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    url = _asset_url("favicon", dt)
    assert url.startswith("/api/v1/branding/asset/favicon?v=")
    assert url.split("v=")[1] == str(int(dt.timestamp()))


def test_allowed_mimes_cover_common_formats():
    assert "image/png" in ALLOWED_ASSET_MIMES
    assert "image/svg+xml" in ALLOWED_ASSET_MIMES
    assert "image/jpeg" in ALLOWED_ASSET_MIMES


@pytest.mark.asyncio
async def test_get_public_branding_merges_defaults_and_assets(db):
    from app.db.models.system import DEFAULT_SETTINGS
    from app.services import settings_service

    # Wipe branding so the default path is exercised.
    s = await settings_service.get(db)
    s.data = {}
    await db.commit()

    # Attach one favicon asset.
    from app.db.models.branding import BrandAsset

    db.add(
        BrandAsset(
            kind="favicon",
            mime="image/png",
            data=b"\x89PNG",
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    await db.commit()

    out = await branding_service.get_public_branding(db)
    defaults = DEFAULT_SETTINGS["branding"]
    assert out["tenant_name"] == defaults["tenant_name"]
    assert out["display"] == defaults["display"]
    assert out["logo_url"] is None
    assert out["favicon_url"] is not None
    assert out["favicon_url"].startswith("/api/v1/branding/asset/favicon?v=")


@pytest.mark.asyncio
async def test_get_public_branding_prefers_stored_values(db):
    from app.services import settings_service

    s = await settings_service.get(db)
    s.data = {"branding": {"tenant_name": "Acme", "display": ""}}
    await db.commit()

    out = await branding_service.get_public_branding(db)
    assert out["tenant_name"] == "Acme"
    # Empty stored value falls back to the default.
    assert out["display"] == "Hermes — 信使"


@pytest.mark.asyncio
async def test_upsert_asset_creates_then_updates(db):
    from sqlalchemy import select

    from app.db.models.branding import BrandAsset

    a = await branding_service.upsert_asset(
        db, "logo", "image/png", b"first"
    )
    assert a.kind == "logo"
    assert a.data == b"first"

    a2 = await branding_service.upsert_asset(
        db, "logo", "image/png", b"second"
    )
    assert a2.data == b"second"
    rows = (await db.execute(select(BrandAsset))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_upsert_asset_rejects_bad_mime(db):
    with pytest.raises(ValueError):
        await branding_service.upsert_asset(db, "favicon", "text/html", b"x")


@pytest.mark.asyncio
async def test_get_asset_and_delete_asset(db):
    await branding_service.upsert_asset(db, "favicon", "image/png", b"x")
    a = await branding_service.get_asset(db, "favicon")
    assert a is not None
    assert await branding_service.asset_meta(db, "favicon") is a

    assert await branding_service.delete_asset(db, "favicon") is True
    assert await branding_service.get_asset(db, "favicon") is None
    assert await branding_service.delete_asset(db, "favicon") is False
