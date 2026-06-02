"""Team / Project / Task DTOs."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


# ── Teams ──
class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    handle: str | None = None
    tagline: str | None = None
    color: str | None = "#b8852a"


class TeamUpdate(BaseModel):
    name: str | None = None
    tagline: str | None = None
    color: str | None = None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    role: str
    status: str
    joined_at: datetime
    name: str | None = None
    email: str | None = None
    initials: str | None = None
    color: str | None = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    handle: str | None
    tagline: str | None
    color: str | None
    plan: str
    join_mode: str
    created_at: datetime


class TeamStats(BaseModel):
    members: int = 0
    agents: int = 0
    threads: int = 0
    knowledge: int = 0


class ActivityItem(BaseModel):
    who: str
    action: str
    target: str = ""
    icon: str = "bolt"
    ago: str = ""


class KnowledgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    size_bytes: int
    uploaded_by_name: str | None = None
    created_at: datetime


class KnowledgeDetail(KnowledgeOut):
    content: str | None = None


class KnowledgeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: str = "doc"
    size_bytes: int = 0
    content: str | None = None


class KnowledgeUpdate(BaseModel):
    name: str | None = None
    kind: str | None = None
    size_bytes: int | None = None
    content: str | None = None


class ConversationBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    primary_agent_id: str
    updated_at: datetime


class TeamDetail(TeamOut):
    my_role: str
    members: list[MemberOut] = Field(default_factory=list)
    shared_agents: list[str] = Field(default_factory=list)
    shared_profile_ids: list[str] = Field(default_factory=list)
    stats: TeamStats = Field(default_factory=TeamStats)
    knowledge: list[KnowledgeOut] = Field(default_factory=list)
    activity: list[ActivityItem] = Field(default_factory=list)
    pinned: list[ConversationBrief] = Field(default_factory=list)


class SharedAgentsUpdate(BaseModel):
    agent_ids: list[str]


class SharedProfilesUpdate(BaseModel):
    profile_ids: list[str]


class AddMemberRequest(BaseModel):
    email: str
    role: str = "member"


class UpdateMemberRequest(BaseModel):
    role: str


# ── Governance ──
class PolicyOut(BaseModel):
    my_role: str
    editable: bool
    permissions: list[dict]  # grouped catalog
    policy: dict             # { perm_id: { role_key: bool } }


class PolicyUpdate(BaseModel):
    policy: dict


# ── Projects ──
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    handle: str | None = None
    color: str | None = "#b8852a"
    icon: str | None = "sparkle"
    summary: str | None = None
    sections: list[str] = Field(default_factory=list)
    pinned_agents: list[str] = Field(default_factory=list)
    deadline: date | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    summary: str | None = None
    color: str | None = None
    icon: str | None = None
    progress: int | None = None
    status: str | None = None
    sections: list[str] | None = None
    pinned_agents: list[str] | None = None
    deadline: date | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    handle: str | None
    color: str | None
    icon: str | None
    summary: str | None
    progress: int
    status: str
    sections: list
    pinned_agents: list
    member_ids: list
    visibility: str
    deadline: date | None
    created_at: datetime


class DocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    size_bytes: int
    created_by_name: str | None = None
    created_at: datetime


class DocCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: str = "doc"
    size_bytes: int = 0


class ProjectDetail(ProjectOut):
    members: list[MemberOut] = Field(default_factory=list)
    docs: list[DocOut] = Field(default_factory=list)
    conversations: list[ConversationBrief] = Field(default_factory=list)


class ProjectMembersUpdate(BaseModel):
    user_ids: list[str]


# ── Tasks ──
class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    owner_id: uuid.UUID | None = None
    agent_id: str | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    owner_id: uuid.UUID | None = None
    agent_id: str | None = None
    order_idx: int | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    status: str
    owner_id: uuid.UUID | None
    agent_id: str | None
    order_idx: int
    created_at: datetime
