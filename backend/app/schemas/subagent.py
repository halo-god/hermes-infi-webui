"""Background subagent DTOs."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SubagentStatus = Literal[
    "starting", "running", "idle", "waiting_input",
    "done", "error", "stopped", "timeout", "interrupted",
]


class SubagentSpawn(BaseModel):
    purpose: str
    initial_prompt: str
    agent_id: str | None = None       # defaults to the parent conversation's primary agent
    profile_id: str | None = None


class SubagentSend(BaseModel):
    text: str


class SubagentOut(BaseModel):
    id: uuid.UUID
    parent_conversation_id: uuid.UUID
    subagent_conversation_id: uuid.UUID
    purpose: str
    agent_id: str
    profile_id: uuid.UUID | None
    status: SubagentStatus
    last_active_at: datetime | None
    error_detail: str | None
    unread_count: int = 0
    last_snippet: str | None = None
    step_count: int = 0
    created_at: datetime
