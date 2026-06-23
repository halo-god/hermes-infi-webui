"""Conversation + Message + GroupMember models."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class ConversationFolder(UUIDPrimaryKey, Timestamps, Base):
    """User-defined folder for grouping personal conversations in the sidebar."""
    __tablename__ = "conversation_folders"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_convo_folder_owner_name"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conv_owner_updated", "owner_id", "updated_at", postgresql_using="btree"),
    )

    title: Mapped[str] = mapped_column(String(200), default="新会话", nullable=False)
    icon: Mapped[str | None] = mapped_column(String(40), default="chat")
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversation_folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(16), default="personal", nullable=False)
    # "personal" = 个人1:1对话, "group" = 群聊（多人+多Agent）
    primary_agent_id: Mapped[str] = mapped_column(String(64), default="hermes")
    active_agent_ids: Mapped[list] = mapped_column(JSONB, default=lambda: ["hermes"])
    profile_id: Mapped[str | None] = mapped_column(String(64))
    acp_session_id: Mapped[str | None] = mapped_column(String(128))
    session_mode: Mapped[str | None] = mapped_column(String(32))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), default="private", nullable=False)
    is_channel: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    channel_mode: Mapped[str] = mapped_column(String(16), default="mention", nullable=False)
    # "off" | "mention" | "always" — 群聊AI回复模式，继承自团队

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    group_members: Mapped[list["GroupMember"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class Message(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_msg_conv_created", "conversation_id", "created_at", postgresql_using="btree"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | agent | roundtable | system
    agent_id: Mapped[str | None] = mapped_column(String(64))
    # {text, markdown?, tool_calls?, merged?, replies?}
    content: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="complete", nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    mentions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 提及列表：Agent id ["hermes"]、人类 "user:{uuid}"、"__all_agents__"、"__all_humans__"
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )  # 引用回复的目标消息
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 撤回(软删)
    # 表情回应：{emoji: [user_id_str, ...]}。用 JSONB 内联存储，避免序列化时触发懒加载关系
    reactions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class GroupMember(UUIDPrimaryKey, Timestamps, Base):
    """群聊成员：人类或Agent。"""
    __tablename__ = "group_members"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    # "admin" = 群主, "member" = 普通成员
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped["Conversation"] = relationship(back_populates="group_members")
