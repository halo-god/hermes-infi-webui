"""Tenant system settings access."""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as env_settings
from app.db.models.system import DEFAULT_SETTINGS, SystemSettings


async def get(db: AsyncSession) -> SystemSettings:
    s = await db.get(SystemSettings, 1)
    if s is None:
        s = SystemSettings(id=1, data=dict(DEFAULT_SETTINGS))
        db.add(s)
        await db.commit()
        await db.refresh(s)
    # self-heal any legacy double-encoded value
    if isinstance(s.data, str):
        s.data = json.loads(s.data)
        await db.commit()
        await db.refresh(s)
    return s


async def update(db: AsyncSession, data: dict) -> SystemSettings:
    s = await get(db)
    s.data = data
    await db.commit()
    await db.refresh(s)
    return s


async def rag_enabled(db: AsyncSession) -> bool:
    """Effective RAG switch: the DB override (system_settings.data.rag.enabled)
    wins when present, otherwise the env setting (settings.rag_enabled) is
    used. Lets an admin toggle vector retrieval from the UI without a restart.
    """
    return await rag_flag(db, "enabled", env_settings.rag_enabled)


async def rag_flag(db: AsyncSession, key: str, default: bool) -> bool:
    """Read one RAG toggle from the DB override (system_settings.data.rag),
    falling back to `default` (an env-driven setting value) when unset."""
    try:
        s = await get(db)
        override = (s.data.get("rag") or {}).get(key)
        if override is not None:
            return bool(override)
    except Exception:  # noqa: BLE001 — settings row issues must never break dispatch
        pass
    return default
