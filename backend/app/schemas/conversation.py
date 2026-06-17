"""Conversation + Message + WorkspaceFile DTOs."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str | None = None
    primary_agent_id: str = "hermes"
    profile_id: str | None = None
    team_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    # Optional first user message to kick off the conversation.
    first_message: str | None = None


class GroupCreate(BaseModel):
    """创建群聊请求。"""
    title: str = Field(min_length=1, max_length=200)
    member_user_ids: list[uuid.UUID] = Field(default_factory=list)
    member_agent_ids: list[str] = Field(default_factory=list)
    team_id: uuid.UUID


class GroupMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    agent_id: str | None = None
    role: str
    joined_at: datetime
    user_name: str | None = None
    last_read_at: datetime | None = None
    presence: str | None = None  # "online" | "offline"; assembled in route


class AddMemberRequest(BaseModel):
    user_id: uuid.UUID | None = None
    agent_id: str | None = None
    role: str = "member"


class ConversationUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    channel_mode: str | None = None


class ReplyRef(BaseModel):
    """被引用消息的精简摘要（用于渲染回复引用条）。"""
    id: uuid.UUID
    role: str
    owner_id: uuid.UUID | None = None
    agent_id: str | None = None
    snippet: str = ""


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    owner_id: uuid.UUID | None = None
    role: str
    agent_id: str | None
    content: dict
    status: str
    mentions: list[str] | None = None
    created_at: datetime
    reply_to_id: uuid.UUID | None = None
    reply_to: ReplyRef | None = None  # 路由内手工组装，避免懒加载
    edited_at: datetime | None = None
    deleted_at: datetime | None = None
    reactions: dict = Field(default_factory=dict)  # {emoji: [user_id_str, ...]}


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    icon: str | None
    type: str = "personal"
    primary_agent_id: str
    active_agent_ids: list[str]
    profile_id: str | None
    team_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    acp_session_id: str | None
    session_mode: str | None = None
    pinned: bool
    visibility: str
    channel_mode: str = "mention"
    created_at: datetime
    updated_at: datetime
    unread: int = 0  # 仅群聊列表填充
    has_mention: bool = False  # 仅群聊列表填充


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100000)
    attached_file_ids: list[str] = Field(default_factory=list)
    skip_agent: bool = False
    mentions: list[str] = Field(default_factory=list)
    profile_id: str | None = None  # override conversation's default profile
    reply_to_id: uuid.UUID | None = None


class MarkReadResponse(BaseModel):
    ok: bool = True
    last_read_at: datetime | None = None


class EditMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100000)


class ReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)


class SetAgentsRequest(BaseModel):
    agent_ids: list[str] = Field(min_length=1)


class SendMessageResponse(BaseModel):
    user_message: MessageOut
    agent_message: MessageOut | None = None


class WorkspaceFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    name: str
    kind: str
    current_version: int
    size_bytes: int
    created_by_agent: str | None
    updated_at: datetime


class WorkspaceFileDetail(WorkspaceFileOut):
    content: str | None = None


class WorkspaceFileVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_id: uuid.UUID
    version_num: int
    content: str | None = None
    size_bytes: int
    created_at: datetime
    author: str | None


class ConfirmRequest(BaseModel):
    request_id: str
    choice: str


class SetSessionModeRequest(BaseModel):
    mode: str = Field(pattern="^(ask|accept_edits|dont_ask)$")


class SetSessionModelRequest(BaseModel):
    model_id: str = Field(min_length=1)
