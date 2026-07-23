"""State machine SOP skills: structured workflow definitions + execution state.

SopSkill defines a directed-graph state machine (nodes + edges + start + terminals).
SopSession tracks the execution state within a conversation (current node, slots, status).

The runner (agent_runner/runner_sop.py) drives the state machine by constructing
per-node prompts and sending them to the ACP agent, controlling tool permissions
and state transitions.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class SopSkill(UUIDPrimaryKey, Timestamps, Base):
    """A state-machine SOP skill definition.

    nodes_json: [{node_id, name, instruction, expected_user_info, allowed_actions, knowledge_scope}]
    edges_json: [{source_node_id, next_node_id, condition, priority, label}]
    start_node_id: the entry node
    terminal_node_ids: nodes where the workflow is considered complete
    trigger_intents: keywords that trigger this SOP (like AgentSkill.trigger_conditions)
    """
    __tablename__ = "sop_skills"

    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    trigger_intents: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    nodes_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    edges_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    start_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    terminal_node_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(String(8), default="true")  # use String for simple toggle

    # Use a property to convert the string toggle to bool
    @property
    def is_enabled(self) -> bool:
        return self.enabled == "true"


class SopSession(UUIDPrimaryKey, Timestamps, Base):
    """Tracks the execution state of an SOP within a conversation.

    status: active (in progress) | completed (reached terminal) | handoff (needs human)
    """
    __tablename__ = "sop_sessions"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    sop_skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sop_skills.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    current_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    slots: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active|completed|handoff
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
