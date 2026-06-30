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
