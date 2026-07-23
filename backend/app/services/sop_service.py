"""SOP skill service: CRUD + trigger matching + state machine helpers."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.sop import SopSession, SopSkill


async def list_sop_skills(db: AsyncSession, profile_id: uuid.UUID | None = None) -> list[SopSkill]:
    stmt = select(SopSkill).order_by(SopSkill.created_at.desc())
    if profile_id:
        stmt = stmt.where(SopSkill.profile_id == profile_id)
    return list((await db.execute(stmt)).scalars().all())


async def get_sop_skill(db: AsyncSession, skill_id: uuid.UUID) -> SopSkill | None:
    return await db.get(SopSkill, skill_id)


async def create_sop_skill(db: AsyncSession, **kwargs) -> SopSkill:
    skill = SopSkill(**kwargs)
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


async def update_sop_skill(db: AsyncSession, skill: SopSkill, **kwargs) -> SopSkill:
    for k, v in kwargs.items():
        setattr(skill, k, v)
    await db.commit()
    await db.refresh(skill)
    return skill


async def delete_sop_skill(db: AsyncSession, skill: SopSkill) -> None:
    await db.delete(skill)
    await db.commit()


async def match_sop_skill(
    db: AsyncSession, profile_id: uuid.UUID | None, query_text: str,
) -> SopSkill | None:
    """Find an enabled SOP skill whose trigger_intents match the user's query."""
    stmt = select(SopSkill).where(SopSkill.enabled == "true")
    if profile_id:
        stmt = stmt.where(SopSkill.profile_id == profile_id)
    skills = list((await db.execute(stmt)).scalars().all())
    query_lower = query_text.lower()
    for skill in skills:
        intents = skill.trigger_intents or []
        if any(isinstance(i, str) and i.lower() in query_lower for i in intents):
            return skill
    return None


async def get_or_create_sop_session(
    db: AsyncSession, conversation_id: uuid.UUID, sop_skill_id: uuid.UUID,
) -> SopSession:
    """Get an active SOP session for a conversation, or create a new one."""
    existing = (
        await db.execute(
            select(SopSession).where(
                SopSession.conversation_id == conversation_id,
                SopSession.status == "active",
            ).order_by(SopSession.created_at.desc()).limit(1)
        )
    ).scalars().first()
    if existing and existing.sop_skill_id == sop_skill_id:
        return existing
    skill = await db.get(SopSkill, sop_skill_id)
    if skill is None:
        raise ValueError("sop skill not found")
    session = SopSession(
        conversation_id=conversation_id,
        sop_skill_id=sop_skill_id,
        current_node_id=skill.start_node_id,
        slots={},
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


def get_node(skill: SopSkill, node_id: str) -> dict | None:
    """Find a node by ID in the skill's nodes_json."""
    for node in (skill.nodes_json or []):
        if node.get("node_id") == node_id:
            return node
    return None


def get_outgoing_edges(skill: SopSkill, node_id: str) -> list[dict]:
    """Get edges originating from a node, sorted by priority."""
    edges = [e for e in (skill.edges_json or []) if e.get("source_node_id") == node_id]
    return sorted(edges, key=lambda e: e.get("priority", 0))


def build_node_prompt(skill: SopSkill, node: dict, slots: dict, user_text: str) -> str:
    """Construct the prompt for a single SOP node execution.

    The prompt instructs the ACP agent on:
    - What the current step is and what to do
    - What user info to collect (expected_user_info)
    - What tools are allowed (allowed_actions)
    - Collected slots so far
    """
    instruction = node.get("instruction", "")
    expected = node.get("expected_user_info", [])
    allowed = node.get("allowed_actions", [])
    node_name = node.get("name", node.get("node_id", ""))

    parts = [f"【当前步骤：{node_name}】"]
    if instruction:
        parts.append(f"执行指令：{instruction}")
    if expected:
        parts.append(f"需要收集的信息：{', '.join(expected)}")
    if allowed:
        parts.append(f"允许的操作：{', '.join(allowed)}")
    if slots:
        slots_str = "\n".join(f"  - {k}: {v}" for k, v in slots.items() if v)
        if slots_str:
            parts.append(f"已收集信息：\n{slots_str}")
    parts.append(f"用户消息：{user_text}")
    parts.append("请根据当前步骤的指令处理用户请求。如果需要更多信息，请询问用户。如果当前步骤已完成，请回复并准备进入下一步。")
    return "\n\n".join(parts)


def check_node_completion(node: dict, slots: dict) -> bool:
    """Check if all expected_user_info for a node has been collected."""
    expected = node.get("expected_user_info", [])
    if not expected:
        return True  # No slots to collect, node is complete
    return all(slots.get(info) for info in expected)


def find_next_node(skill: SopSkill, current_node_id: str, slots: dict) -> str | None:
    """Determine the next node based on edge conditions.

    Simple condition matching: if edge has no condition, it's the default.
    If it has a condition string, check if it matches a slot value.
    """
    edges = get_outgoing_edges(skill, current_node_id)
    if not edges:
        return None
    # First pass: try condition-based edges
    for edge in edges:
        condition = edge.get("condition")
        if condition and isinstance(condition, str):
            # Simple "slot=value" or "slot:has_value" matching
            if "=" in condition:
                key, val = condition.split("=", 1)
                if str(slots.get(key.strip(), "")) == val.strip():
                    return edge.get("next_node_id")
            elif condition in slots and slots[condition]:
                return edge.get("next_node_id")
    # Fallback: first edge (default transition)
    return edges[0].get("next_node_id")
