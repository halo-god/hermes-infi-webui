"""Gallery API: browse, publish, and copy shared resources."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models.gallery import GalleryItem
from app.db.models.user import User
from app.deps import get_current_user
from app.core.guards import require_admin

router = APIRouter()


class GalleryPublish(BaseModel):
    type: str  # profile|sop|knowledge|tool
    item_id: str
    name: str
    description: str = ""
    icon: str = "sparkle"
    color: str = "#b8852a"
    category: str | None = None
    snapshot: dict = {}


class GalleryItemOut(BaseModel):
    id: str
    type: str
    item_id: str
    name: str
    description: str
    icon: str
    color: str
    category: str | None
    published_by_name: str | None
    published_at: str | None
    download_count: int
    snapshot: dict

    class Config:
        from_attributes = True


def _serialize(g: GalleryItem) -> dict:
    return {
        "id": str(g.id),
        "type": g.type,
        "item_id": g.item_id,
        "name": g.name,
        "description": g.description,
        "icon": g.icon,
        "color": g.color,
        "category": g.category,
        "published_by_name": g.published_by_name,
        "published_at": g.published_at.isoformat() if g.published_at else None,
        "download_count": g.download_count,
        "snapshot": g.snapshot_json,
    }


@router.get("")
async def list_gallery(
    type: str | None = Query(None, pattern="^(profile|sop|knowledge|tool)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all published gallery items, optionally filtered by type."""
    stmt = select(GalleryItem).order_by(GalleryItem.download_count.desc(), GalleryItem.published_at.desc())
    if type:
        stmt = stmt.where(GalleryItem.type == type)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def publish_to_gallery(
    payload: GalleryPublish,
    user: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Publish a resource to the gallery (admin only)."""
    # Check if already published
    existing = (
        await db.execute(
            select(GalleryItem).where(
                GalleryItem.type == payload.type,
                GalleryItem.item_id == payload.item_id,
            )
        )
    ).scalars().first()
    if existing:
        # Update existing
        existing.name = payload.name
        existing.description = payload.description
        existing.icon = payload.icon
        existing.color = payload.color
        existing.category = payload.category
        existing.snapshot_json = payload.snapshot
        existing.published_at = datetime.now(timezone.utc)
        await db.commit()
        return _serialize(existing)

    item = GalleryItem(
        type=payload.type,
        item_id=payload.item_id,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        color=payload.color,
        category=payload.category,
        published_by=user.id,
        published_by_name=user.name,
        published_at=datetime.now(timezone.utc),
        snapshot_json=payload.snapshot,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _serialize(item)


@router.delete("/{gallery_id}", status_code=204)
async def remove_from_gallery(
    gallery_id: uuid.UUID,
    user: User = Depends(require_admin()),
    db: AsyncSession = Depends(get_db),
):
    """Remove a resource from the gallery (admin only)."""
    item = await db.get(GalleryItem, gallery_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Gallery item not found")
    await db.delete(item)
    await db.commit()


@router.post("/{gallery_id}/copy")
async def copy_from_gallery(
    gallery_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Copy a gallery resource to the user's workspace.

    Returns the new resource's ID and type-specific details.
    For profiles: creates a new Profile with a cloned handle.
    For SOPs: creates a new SopSkill owned by the user's active profile.
    For knowledge/tools: returns the snapshot for manual import.
    """
    item = await db.get(GalleryItem, gallery_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Gallery item not found")

    # Increment download count
    item.download_count += 1
    await db.commit()

    snapshot = item.snapshot_json or {}
    new_id = None

    if item.type == "profile":
        from app.db.models.agent import Profile
        import secrets
        handle = snapshot.get("handle", "cloned") + "-" + secrets.token_hex(3)
        profile = Profile(
            name=snapshot.get("name", item.name) + " (副本)",
            handle=handle,
            scope="personal",
            color=snapshot.get("color", item.color),
            icon=snapshot.get("icon", item.icon),
            desc=snapshot.get("desc", item.description),
            default_agent_id=snapshot.get("default_agent_id", "hermes"),
            default_model=snapshot.get("default_model", "hermes-4"),
            system_prompt=snapshot.get("system_prompt"),
            skills=snapshot.get("skills"),
            knowledge_ids=snapshot.get("knowledge_ids", []),
            knowledge_folder_ids=snapshot.get("knowledge_folder_ids", []),
            knowledge_team_ids=snapshot.get("knowledge_team_ids", []),
            mcp_server_names=snapshot.get("mcp_server_names", []),
            employee_no=None,
            department=snapshot.get("department"),
            position=snapshot.get("position"),
            employee_status="active",
        )
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
        new_id = str(profile.id)

    elif item.type == "sop":
        from app.db.models.sop import SopSkill
        sop = SopSkill(
            name=snapshot.get("name", item.name) + " (副本)",
            description=snapshot.get("description", item.description),
            trigger_intents=snapshot.get("trigger_intents", []),
            nodes_json=snapshot.get("nodes", []),
            edges_json=snapshot.get("edges", []),
            start_node_id=snapshot.get("start_node_id", "start"),
            terminal_node_ids=snapshot.get("terminal_node_ids", []),
            enabled="true",
        )
        db.add(sop)
        await db.commit()
        await db.refresh(sop)
        new_id = str(sop.id)

    else:
        # For knowledge/tools, return the snapshot for manual import
        return {"message": "Snapshot returned for manual import", "snapshot": snapshot, "type": item.type}

    return {"new_id": new_id, "type": item.type, "name": item.name}
