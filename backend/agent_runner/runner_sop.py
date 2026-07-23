"""SOP state machine runner: drives the workflow by sending per-node prompts
to the ACP agent and controlling state transitions.

This is a simplified SOP executor that wraps the existing ACP prompt flow:
1. Match SOP skill by trigger_intents
2. Get or create SopSession (tracks current node + slots)
3. Build node-specific prompt (instruction + expected_user_info + allowed_actions)
4. Send to ACP agent via the normal send_message path
5. After response, check if node is complete -> transition to next node
6. When terminal node reached, mark session as completed
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.sop import SopSession, SopSkill
from app.services import sop_service

logger = logging.getLogger(__name__)


async def try_match_and_run_sop(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    user_text: str,
) -> dict | None:
    """Check if the user's message matches an SOP skill trigger.

    Returns a dict with SOP context if matched, None otherwise.
    The caller (dispatch) uses this to decide whether to run the SOP
    prompt instead of the normal system_prompt path.
    """
    skill = await sop_service.match_sop_skill(db, profile_id, user_text)
    if skill is None:
        return None

    session = await sop_service.get_or_create_sop_session(db, conversation_id, skill.id)
    node = sop_service.get_node(skill, session.current_node_id)
    if node is None:
        logger.warning("SOP session %s: current node %s not found in skill %s",
                       session.id, session.current_node_id, skill.id)
        return None

    # Build the SOP prompt for this node
    sop_prompt = sop_service.build_node_prompt(skill, node, session.slots, user_text)

    return {
        "sop_skill": skill,
        "sop_session": session,
        "sop_prompt": sop_prompt,
        "node": node,
    }


async def advance_sop_state(
    db: AsyncSession,
    session: SopSession,
    skill: SopSkill,
    agent_response: str,
    extracted_slots: dict | None = None,
) -> str:
    """After the ACP agent responds, advance the SOP state machine.

    1. Merge any extracted slots into the session
    2. Check if the current node is complete
    3. If complete, find the next node via edges
    4. If terminal, mark as completed
    5. Return the new current_node_id (or None if completed)
    """
    # Merge extracted slots
    if extracted_slots:
        session.slots = {**(session.slots or {}), **extracted_slots}

    node = sop_service.get_node(skill, session.current_node_id)
    if node is None:
        return session.current_node_id

    # Check completion
    if not sop_service.check_node_completion(node, session.slots):
        # Node not complete yet - stay on current node
        await db.commit()
        return session.current_node_id

    # Node complete - find next
    next_node_id = sop_service.find_next_node(skill, session.current_node_id, session.slots)
    if next_node_id is None or next_node_id in (skill.terminal_node_ids or []):
        # Reached terminal or no outgoing edges
        session.status = "completed"
        from datetime import datetime, timezone
        session.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return next_node_id or ""

    # Transition to next node
    session.current_node_id = next_node_id
    await db.commit()
    return next_node_id
