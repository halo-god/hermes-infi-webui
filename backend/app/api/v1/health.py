"""Liveness / readiness probes."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.redis import get_redis
from app.db.base import async_session_maker

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict:
    checks: dict[str, str] = {}
    # DB
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {exc.__class__.__name__}"
    # Redis
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc.__class__.__name__}"

    ready = all(v == "ok" for v in checks.values())
    return {"ready": ready, "checks": checks}


@router.get("/info")
async def info() -> dict:
    """Return system information for monitoring."""
    import platform
    import sys
    from app.config import settings

    return {
        "app": settings.app_name,
        "version": "0.1.0",
        "environment": settings.environment,
        "python": sys.version,
        "platform": platform.platform(),
    }


@router.get("/config")
async def get_config() -> dict:
    from app.config import settings
    return {
        "features": {
            "followup_chips": settings.feature_followup_chips,
        },
    }
