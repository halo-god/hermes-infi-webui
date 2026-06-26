"""Feedback DTOs."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FeedbackCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    category: str = Field(default="bug", max_length=16)


class FeedbackUpdate(BaseModel):
    """Admin-only update: status, priority, reply."""
    status: str | None = Field(default=None, max_length=16)
    priority: str | None = Field(default=None, max_length=8)
    reply: str | None = None


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: uuid.UUID
    user_name: str
    title: str
    content: str
    category: str
    status: str
    priority: str
    reply: str | None = None
    replied_by: uuid.UUID | None = None
    replied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
