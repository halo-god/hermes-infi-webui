"""Admin console DTOs."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class AdminUserUpdate(BaseModel):
    role: str | None = None
    status: str | None = None
    department: str | None = None
    title: str | None = None
    is_active: bool | None = None


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    actor_id: uuid.UUID | None
    actor_name: str | None
    action: str
    target: str | None
    ip: str | None
    result: str
    meta: dict


class SystemSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    data: dict
    updated_at: datetime


class SystemSettingsUpdate(BaseModel):
    data: dict


class BrandingPublic(BaseModel):
    """Unauthenticated, front-facing branding payload."""

    tenant_name: str
    display: str
    short_name: str
    login_tagline: str
    login_subtitle: str
    accent: str
    favicon_url: str | None = None
    logo_url: str | None = None


class BrandAssetOut(BaseModel):
    """Metadata echo after an admin asset upload/delete."""

    kind: str
    mime: str
    updated_at: datetime
    url: str


class AdminStats(BaseModel):
    users: int
    teams: int
    conversations: int
    messages: int
    agents: int
    active_users: int = 0
    pending_users: int = 0
    role_distribution: dict[str, int] = {}
    source_distribution: dict[str, int] = {}


# ── roles & permission matrix ──
class RoleOut(BaseModel):
    id: str
    name: str
    desc: str
    system: bool
    users: int


class PermissionItem(BaseModel):
    id: str
    name: str
    roles: list[str]


class PermissionGroup(BaseModel):
    group: str
    items: list[PermissionItem]


class RolesMatrixOut(BaseModel):
    roles: list[RoleOut]
    permissions: list[PermissionGroup]


# ── identity providers ──
class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    enabled: bool
    config: dict


class ProviderUpdate(BaseModel):
    enabled: bool | None = None
    config: dict | None = None


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_id: str
    org_id: str | None = None
    match_basis: str
    source_value: str
    dept: str | None
    default_role: str
    auto_join_team_id: uuid.UUID | None


class MappingCreate(BaseModel):
    org_id: str | None = None
    match_basis: str = "attribute"
    source_value: str
    dept: str | None = None
    default_role: str = "member"
    auto_join_team_id: uuid.UUID | None = None

    @field_validator("source_value")
    @classmethod
    def source_value_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_value 不能为空")
        return v


# ── session logs (会话日志) ──
class SessionLogItem(BaseModel):
    id: str
    title: str | None = None
    source: str  # personal=对话 | group=工作
    user_name: str | None = None
    user_email: str | None = None
    first_input: str | None = None
    last_output: str | None = None
    status: str  # success | fail | running | cancelled
    turn_count: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    duration_ms: int | None = None
    session_id: str
    acp_session_id: str | None = None
    last_activity: datetime | None = None
    created_at: datetime | None = None


class SessionLogListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SessionLogItem]


class SessionCallOut(BaseModel):
    id: int
    kind: str  # model | tool
    name: str | None = None
    tool_kind: str | None = None
    status: str
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class RoundtableReplyOut(BaseModel):
    """One AI's reply inside a roundtable turn (group chat / MoA)."""
    agent_id: str
    profile_id: str | None = None
    text: str = ""
    status: str = "complete"
    thinking: str | None = None
    calls: list[SessionCallOut] = []


class SessionExecutionOut(BaseModel):
    """One turn in the conversation: user input + assistant reply + calls."""
    index: int
    user_text: str | None = None
    # Sender of this turn's user message (group chats: who actually typed it).
    user_name: str | None = None
    agent_text: str | None = None
    thinking: str | None = None
    status: str
    duration_ms: int | None = None
    message_id: str | None = None
    calls: list[SessionCallOut] = []
    # Roundtable (group chat) turns carry per-AI replies; agent_text is the
    # merged summary.
    replies: list[RoundtableReplyOut] | None = None


class SessionLogDetail(BaseModel):
    id: str
    title: str | None = None
    source: str
    user_name: str | None = None
    user_email: str | None = None
    first_input: str | None = None
    last_output: str | None = None
    status: str
    turn_count: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    duration_ms: int | None = None
    session_id: str
    acp_session_id: str | None = None
    agent: str | None = None
    profile_name: str | None = None
    profile_id: str | None = None
    team_id: str | None = None
    created_at: datetime | None = None
    last_activity: datetime | None = None
    turns: list[SessionExecutionOut] = []
