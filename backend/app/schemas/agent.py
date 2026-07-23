"""Agent + Profile DTOs."""
from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, ConfigDict, field_validator


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    kind: str
    available: bool
    official: bool
    version: str | None
    color: str | None
    icon: str | None
    description: str | None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    handle: str
    scope: str  # personal | team | global
    color: str
    icon: str
    desc: str
    default_agent_id: str
    default_model: str
    team_id: uuid.UUID | None = None
    is_active: bool = True
    path: str | None = None
    system_prompt: str | None = None
    skills: list[str] = []
    featured: bool = False
    knowledge_ids: list[str] = []
    knowledge_folder_ids: list[str] = []
    knowledge_team_ids: list[str] = []
    mcp_server_names: list[str] = []
    is_moa: bool = False
    moa_target_profile_ids: list[str] = []
    # P0-2: per-turn tool_call circuit breaker (0 = disabled).
    max_iterations: int = 50
    # P1-3: per-stage prompt + tool subset (see migration 0059 for shape).
    staged_prompts: dict | None = None
    staged_enabled: bool = False
    is_chain: bool = False
    chain_target_profile_ids: list[str] = []
    is_research: bool = False

    @field_validator("skills", mode="before")
    @classmethod
    def _parse_skills(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError):
                return []
        return []


class ProfileCreate(BaseModel):
    name: str
    handle: str
    scope: str = "personal"
    color: str = "#b8852a"
    icon: str = "brand"
    desc: str = ""
    default_agent_id: str = "hermes"
    default_model: str = "hermes-4"
    team_id: uuid.UUID | None = None
    system_prompt: str | None = None
    skills: list[str] = []
    featured: bool = False
    knowledge_ids: list[str] = []
    knowledge_folder_ids: list[str] = []
    knowledge_team_ids: list[str] = []
    max_iterations: int = 50
    staged_prompts: dict | None = None
    staged_enabled: bool = False
    is_chain: bool = False
    chain_target_profile_ids: list[str] = []
    is_research: bool = False


class ProfileUpdate(BaseModel):
    name: str | None = None
    handle: str | None = None
    scope: str | None = None
    color: str | None = None
    icon: str | None = None
    desc: str | None = None
    default_agent_id: str | None = None
    default_model: str | None = None
    team_id: uuid.UUID | None = None
    is_active: bool | None = None
    path: str | None = None
    system_prompt: str | None = None
    skills: list[str] | None = None
    featured: bool | None = None
    knowledge_ids: list[str] | None = None
    knowledge_folder_ids: list[str] | None = None
    knowledge_team_ids: list[str] | None = None
    mcp_server_names: list[str] | None = None
    is_moa: bool | None = None
    moa_target_profile_ids: list[str] | None = None
    max_iterations: int | None = None
    staged_prompts: dict | None = None
    staged_enabled: bool | None = None
    is_chain: bool | None = None
    chain_target_profile_ids: list[str] | None = None
    is_research: bool | None = None


class ProfileExport(BaseModel):
    name: str
    handle: str
    scope: str
    color: str
    icon: str
    desc: str
    default_agent_id: str
    default_model: str
    system_prompt: str | None = None
    skills: list[str] = []
    featured: bool = False


class ScanProfilesResponse(BaseModel):
    created: int
    message: str
    version: str
    profiles_found: int
    hermes_path: str | None = None   # absolute path where hermes binary was found
    hermes_home: str | None = None   # hermes home directory that was scanned
    errors: list[str] = []           # non-fatal warnings / errors encountered
