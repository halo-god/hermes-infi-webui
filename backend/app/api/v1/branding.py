"""Branding: public read-out + admin asset upload/delete.

Public endpoints (no auth) so the login page, boot screen, document title and
favicon can be driven by tenant config before the user is authenticated. Admin
write endpoints live under ``/admin/settings/asset`` and reuse the platform
admin guard.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File as FastApiFile, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.files import read_upload_capped
from app.core.rbac import require_admin
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.admin import BrandAssetOut, BrandingPublic
from app.services import audit_service, branding_service

router = APIRouter()


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _asset_url(kind: str, a) -> str:
    ts = int(a.updated_at.timestamp())
    return f"/api/v1/branding/asset/{kind}?v={ts}"


# ── public read ──────────────────────────────────────────────────────
@router.get("/branding", response_model=BrandingPublic)
async def get_public_branding(db: AsyncSession = Depends(get_db)):
    """Drives login page, boot screen, document title, favicon, accent."""
    return await branding_service.get_public_branding(db)


@router.get("/branding/asset/{kind}")
async def get_asset_raw(kind: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        a = await branding_service.get_asset(db, kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if a is None:
        raise HTTPException(status_code=404, detail="资源未上传")
    etag = f'"{kind}-{int(a.updated_at.timestamp())}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(
        content=a.data,
        media_type=a.mime,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── admin write (asset upload/delete) ────────────────────────────────
@router.post("/admin/settings/asset", response_model=BrandAssetOut)
async def upload_asset(
    request: Request,
    kind: str = Form(...),
    file: UploadFile = FastApiFile(...),
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    raw = await read_upload_capped(file, settings.max_upload_bytes)
    mime = (file.content_type or "").lower()
    try:
        a = await branding_service.upsert_asset(db, kind, mime, raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await audit_service.record(
        action="admin.settings.asset_update",
        actor_id=admin.id,
        actor_name=admin.name,
        target=f"branding_asset:{a.kind}",
        ip=_ip(request),
        meta={"kind": a.kind, "mime": a.mime, "size": len(raw)},
    )
    return BrandAssetOut(kind=a.kind, mime=a.mime, updated_at=a.updated_at, url=_asset_url(a.kind, a))


@router.delete("/admin/settings/asset/{kind}")
async def delete_asset(
    kind: str,
    request: Request,
    admin: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    try:
        removed = await branding_service.delete_asset(db, kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await audit_service.record(
        action="admin.settings.asset_delete",
        actor_id=admin.id,
        actor_name=admin.name,
        target=f"branding_asset:{kind}",
        ip=_ip(request),
        meta={"kind": kind, "removed": removed},
    )
    return {"ok": True, "removed": removed}
