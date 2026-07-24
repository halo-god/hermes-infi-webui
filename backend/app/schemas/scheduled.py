"""Scheduled task DTOs."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScheduledTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    agent_id: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1)
    cron: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class ScheduledTaskUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    agent_id: str | None = Field(default=None, min_length=1, max_length=64)
    prompt: str | None = Field(default=None, min_length=1)
    cron: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None


class ScheduledTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    agent_id: str
    prompt: str
    cron: str
    enabled: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_status: str | None = None
    success_count: int = 0
    fail_count: int = 0
    created_at: datetime
    updated_at: datetime
