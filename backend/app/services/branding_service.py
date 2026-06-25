"""Public branding read-out + admin asset upsert/delete.

Brand text lives in ``system_settings.data.branding`` (text JSON); binary
favicon/logo bytes live in the separate ``branding_assets`` table. This module
joins the two into a single public-facing payload and provides the admin write
operations on assets.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.branding import ASSET_KINDS, BrandAsset
from app.db.models.system import DEFAULT_SETTINGS
from app.services import settings_service

#: MIME types accepted for favicon/logo uploads.
ALLOWED_ASSET_MIMES = {
    "image/png",
    "image/svg+xml",
    "image/webp",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/jpeg",
}


def _normalize_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k not in ASSET_KINDS:
        raise ValueError(f"不支持的资源类型: {kind!r}")
    return k


def _asset_url(kind: str, updated_at: datetime) -> str:
    """Cache-bust via the asset's updated_at timestamp."""
    ts = int(updated_at.timestamp())
    return f"/api/v1/branding/asset/{kind}?v={ts}"


async def get_public_branding(db: AsyncSession) -> dict:
    """Return the unauthenticated, front-facing branding payload.

    Merges stored branding over the defaults so missing keys never 500, and
    attaches favicon/logo URLs (``None`` when no asset has been uploaded).
    """
    s = await settings_service.get(db)
    stored = (s.data or {}).get("branding") or {}
    defaults = DEFAULT_SETTINGS["branding"]
    branding = {**defaults, **stored}

    # Helper: use stored value if non-empty, else fall back to default.
    def _field(key: str) -> str:
        val = branding.get(key, defaults[key])
        return val if val else defaults[key]

    out: dict = {
        "tenant_name": _field("tenant_name"),
        "display": _field("display"),
        "short_name": _field("short_name"),
        "login_tagline": _field("login_tagline"),
        "login_subtitle": _field("login_subtitle"),
        "accent": branding.get("accent", defaults["accent"]),
        "favicon_url": None,
        "logo_url": None,
    }

    rows = (await db.execute(select(BrandAsset))).scalars().all()
    for a in rows:
        url = _asset_url(a.kind, a.updated_at)
        if a.kind == "favicon":
            out["favicon_url"] = url
        elif a.kind == "logo":
            out["logo_url"] = url
    return out


async def get_asset(db: AsyncSession, kind: str) -> BrandAsset | None:
    k = _normalize_kind(kind)
    return await db.get(BrandAsset, k)


async def asset_meta(db: AsyncSession, kind: str) -> BrandAsset | None:
    """Same as get_asset — kept for readable call sites."""
    return await get_asset(db, kind)


async def upsert_asset(
    db: AsyncSession, kind: str, mime: str, data: bytes
) -> BrandAsset:
    """Insert or replace an asset's bytes/mime, bumping updated_at."""
    k = _normalize_kind(kind)
    if mime not in ALLOWED_ASSET_MIMES:
        raise ValueError(f"不支持的图片类型: {mime!r}")

    a = await db.get(BrandAsset, k)
    if a is None:
        a = BrandAsset(kind=k, mime=mime, data=data)
        db.add(a)
    else:
        a.mime = mime
        a.data = data
        a.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(a)
    return a


async def delete_asset(db: AsyncSession, kind: str) -> bool:
    """Delete an asset; return True if a row was actually removed."""
    k = _normalize_kind(kind)
    a = await db.get(BrandAsset, k)
    if a is None:
        return False
    await db.delete(a)
    await db.commit()
    return True
