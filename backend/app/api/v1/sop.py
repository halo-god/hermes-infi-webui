"""SOP skill management API."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models.user import User
from app.deps import get_current_user
from app.core.guards import require_permission
from app.services import sop_service

router = APIRouter()


class SopNodeIn(BaseModel):
    node_id: str
    name: str = ""
    instruction: str = ""
    expected_user_info: list[str] = []
    allowed_actions: list[str] = []
    knowledge_scope: dict = {}


class SopEdgeIn(BaseModel):
    source_node_id: str
    next_node_id: str
    condition: str | None = None
    priority: int = 0
    label: str = ""


class SopSkillCreate(BaseModel):
    name: str
    description: str = ""
    profile_id: uuid.UUID | None = None
    trigger_intents: list[str] = []
    nodes: list[SopNodeIn] = []
    edges: list[SopEdgeIn] = []
    start_node_id: str
    terminal_node_ids: list[str] = []


class SopSkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger_intents: list[str] | None = None
    nodes: list[SopNodeIn] | None = None
    edges: list[SopEdgeIn] | None = None
    start_node_id: str | None = None
    terminal_node_ids: list[str] | None = None
    enabled: bool | None = None


@router.get("")
async def list_sop_skills(
    profile_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skills = await sop_service.list_sop_skills(db, profile_id)
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "description": s.description,
            "profile_id": str(s.profile_id) if s.profile_id else None,
            "trigger_intents": s.trigger_intents,
            "nodes": s.nodes_json,
            "edges": s.edges_json,
            "start_node_id": s.start_node_id,
            "terminal_node_ids": s.terminal_node_ids,
            "enabled": s.is_enabled,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in skills
    ]


@router.post("", status_code=201)
async def create_sop_skill(
    payload: SopSkillCreate,
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    skill = await sop_service.create_sop_skill(
        db,
        name=payload.name,
        description=payload.description,
        profile_id=payload.profile_id,
        trigger_intents=payload.trigger_intents,
        nodes_json=[n.model_dump() for n in payload.nodes],
        edges_json=[e.model_dump() for e in payload.edges],
        start_node_id=payload.start_node_id,
        terminal_node_ids=payload.terminal_node_ids,
    )
    return {"id": str(skill.id), "name": skill.name}


@router.put("/{skill_id}")
async def update_sop_skill(
    skill_id: uuid.UUID,
    payload: SopSkillUpdate,
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    skill = await sop_service.get_sop_skill(db, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="SOP skill not found")
    updates = payload.model_dump(exclude_unset=True)
    if "nodes" in updates and updates["nodes"] is not None:
        updates["nodes_json"] = [n.model_dump() if isinstance(n, SopNodeIn) else n for n in updates.pop("nodes")]
    if "edges" in updates and updates["edges"] is not None:
        updates["edges_json"] = [e.model_dump() if isinstance(e, SopEdgeIn) else e for e in updates.pop("edges")]
    if "enabled" in updates and updates["enabled"] is not None:
        updates["enabled"] = "true" if updates.pop("enabled") else "false"
    await sop_service.update_sop_skill(db, skill, **updates)
    return {"id": str(skill.id)}


@router.delete("/{skill_id}", status_code=204)
async def delete_sop_skill(
    skill_id: uuid.UUID,
    user: User = Depends(require_permission("agent.manage")),
    db: AsyncSession = Depends(get_db),
):
    skill = await sop_service.get_sop_skill(db, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="SOP skill not found")
    await sop_service.delete_sop_skill(db, skill)
