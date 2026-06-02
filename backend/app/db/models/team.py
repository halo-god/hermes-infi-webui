"""Team, TeamMember, Project, ProjectTask models."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class Team(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    tagline: Mapped[str | None] = mapped_column(String(200))
    color: Mapped[str | None] = mapped_column(String(16), default="#b8852a")
    plan: Mapped[str] = mapped_column(String(24), default="team")
    join_mode: Mapped[str] = mapped_column(String(24), default="invite")
    policy: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    shared_agents: Mapped[list] = mapped_column(JSONB, default=lambda: ["hermes"])
    channel_mode: Mapped[str] = mapped_column(String(16), default="mention", nullable=False)
    invite_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invite_role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)

    members: Mapped[list["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamMember(Timestamps, Base):
    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="offline")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    team: Mapped["Team"] = relationship(back_populates="members")


class Project(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "projects"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(16), default="#b8852a")
    icon: Mapped[str | None] = mapped_column(String(40), default="sparkle")
    summary: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|archived
    sections: Mapped[list] = mapped_column(JSONB, default=list)
    pinned_agents: Mapped[list] = mapped_column(JSONB, default=list)
    member_ids: Mapped[list] = mapped_column(JSONB, default=list)
    visibility: Mapped[str] = mapped_column(String(16), default="team")
    deadline: Mapped[date | None] = mapped_column(Date)

    team: Mapped["Team"] = relationship(back_populates="projects")
    tasks: Mapped[list["ProjectTask"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectTask.order_idx"
    )


class ProjectTask(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "project_tasks"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="todo")  # todo|doing|done
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    agent_id: Mapped[str | None] = mapped_column(String(64))
    order_idx: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped["Project"] = relationship(back_populates="tasks")


class TeamKnowledge(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "team_knowledge"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="doc")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    uploaded_by_name: Mapped[str | None] = mapped_column(String(120))


class ProjectDoc(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "project_docs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="doc")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_by_name: Mapped[str | None] = mapped_column(String(120))
