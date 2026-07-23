"""Agent registry - populated by the Agent Runner's ACP discovery scan."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class Agent(Timestamps, Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="acp_cli")  # acp_cli | builtin_mock
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    official: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str | None] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(16))
    icon: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    command: Mapped[list] = mapped_column(JSONB, default=list)  # spawn argv
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Profile(UUIDPrimaryKey, Timestamps, Base):
    """Digital Employee profile - a personified AI assistant with HR attributes.

    Maps to the "digital employee" concept: name=employee name, system_prompt=job
    description, skills=capabilities, knowledge_ids=work materials, mcp_server_names=
    tool permissions. The employee_* fields add HR semantics (department, position,
    status, hire date) on top of the existing technical configuration.
    """

    __tablename__ = "profiles"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    handle: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), default="personal")  # personal|team|global
    color: Mapped[str] = mapped_column(String(16), default="#b8852a")
    icon: Mapped[str] = mapped_column(String(40), default="brand")
    desc: Mapped[str] = mapped_column(Text, default="")
    default_agent_id: Mapped[str] = mapped_column(String(64), default="hermes")
    default_model: Mapped[str] = mapped_column(String(64), default="hermes-4")
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON array e.g. '["coding","analysis"]'
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Bound team_knowledge / project_docs ids - their content is injected into system_prompt.
    knowledge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Bound knowledge FOLDER ids - all items under these folders are injected.
    knowledge_folder_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Bound whole TEAM ids - every non-folder TeamKnowledge item under these teams is injected.
    knowledge_team_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Names of admin-registered MCP servers (system_settings.mcp_servers) this
    # profile's ACP sessions should be started with. Empty = no MCP tools.
    mcp_server_names: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # MoA ("mixture of agents"): selecting this profile fans a message out to
    # moa_target_profile_ids via the existing roundtable executor and returns
    # one synthesized reply, instead of answering with default_agent_id itself.
    is_moa: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    moa_target_profile_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # ── Digital Employee HR attributes ──
    employee_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    position: Mapped[str | None] = mapped_column(String(120), nullable=True)
    employee_status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active|leave|archived
    hired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmployeeWorkRecord(UUIDPrimaryKey, Timestamps, Base):
    """Work record (activity log) for a digital employee.

    Created after each completed agent turn - captures what the employee did,
    how many tokens it consumed, how long it took, and whether the user gave
    positive/negative feedback (from message reactions).
    """

    __tablename__ = "employee_work_records"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(16), default="chat", nullable=False)  # chat|task|skill|tool|knowledge
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)  # positive|negative|None
