"""Agent registry — populated by the Agent Runner's ACP discovery scan."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
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
    # Bound team_knowledge / project_docs ids — their content is injected into system_prompt.
    knowledge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Bound knowledge FOLDER ids — all items under these folders are injected.
    knowledge_folder_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Names of admin-registered MCP servers (system_settings.mcp_servers) this
    # profile's ACP sessions should be started with. Empty = no MCP tools.
    mcp_server_names: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # MoA ("mixture of agents"): selecting this profile fans a message out to
    # moa_target_profile_ids via the existing roundtable executor and returns
    # one synthesized reply, instead of answering with default_agent_id itself.
    is_moa: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    moa_target_profile_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
