"""Team, TeamMember, Project, ProjectTask models."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey

try:
    from pgvector.sqlalchemy import Vector
    _PGVECTOR_AVAILABLE = True
except ImportError:  # pragma: no cover — pgvector is a declared dep but envs
    # lacking it (e.g. a stripped CI) still import this module for the other
    # models; Vector-typed columns simply aren't used in such envs.
    Vector = None  # type: ignore[assignment,misc]
    _PGVECTOR_AVAILABLE = False


class Team(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    tagline: Mapped[str | None] = mapped_column(String(200))
    color: Mapped[str | None] = mapped_column(String(16), default="#b8852a")
    plan: Mapped[str] = mapped_column(String(24), default="team")
    join_mode: Mapped[str] = mapped_column(String(24), default="invite")
    policy: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    shared_profile_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
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
    __table_args__ = (
        Index("ix_team_member_user_team", "user_id", "team_id", postgresql_using="btree"),
    )

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
    pinned_profile_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    member_ids: Mapped[list] = mapped_column(JSONB, default=list)
    visibility: Mapped[str] = mapped_column(String(16), default="team")
    deadline: Mapped[date | None] = mapped_column(Date)

    team: Mapped["Team"] = relationship(back_populates="projects")
    tasks: Mapped[list["ProjectTask"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectTask.order_idx"
    )


class ProjectTask(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "project_tasks"
    __table_args__ = (
        Index("ix_project_tasks_project_order", "project_id", "order_idx"),
    )

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
    description: Mapped[str | None] = mapped_column(Text)
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

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
    storage_key: Mapped[str | None] = mapped_column(String(512))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    uploaded_by_name: Mapped[str | None] = mapped_column(String(120))
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    folder_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    is_folder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1)


class TeamKnowledgeVersion(UUIDPrimaryKey, Base):
    __tablename__ = "team_knowledge_versions"

    team_knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_knowledge.id", ondelete="CASCADE"), index=True
    )
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    author: Mapped[str | None] = mapped_column(String(120))


class ProjectDoc(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "project_docs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="doc")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_name: Mapped[str | None] = mapped_column(String(120))
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    folder_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    is_folder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1)


class ProjectDocVersion(UUIDPrimaryKey, Base):
    __tablename__ = "project_doc_versions"

    project_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_docs.id", ondelete="CASCADE"), index=True
    )
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    author: Mapped[str | None] = mapped_column(String(120))


class ProjectActivity(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "project_activity"
    __table_args__ = (
        Index("ix_project_activity_project", "project_id"),
        Index("ix_project_activity_team_created", "team_id", "created_at"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_name: Mapped[str | None] = mapped_column(String(120))
    # task.created|task.moved|task.done|doc.created|knowledge.created|task.derived
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class TeamKnowledgeChunk(UUIDPrimaryKey, Timestamps, Base):
    """One embedding-indexed slice of a TeamKnowledge (or ProjectDoc) item.

    P1-1 RAG: instead of injecting whole documents into the system prompt,
    _build_knowledge_prompt() embeds the user's query and fetches the top-k
    most relevant chunks via pgvector cosine search. `embedding` is nullable
    so indexing can fail per-chunk without losing the text.
    """
    __tablename__ = "team_knowledge_chunks"
    __table_args__ = (
        # Unique per source: (knowledge_id, chunk_index) for team knowledge,
        # (project_doc_id, chunk_index) for project docs. Each source type uses
        # its own unique partial index implicitly via the nullable FK columns.
        Index("ix_team_knowledge_chunks_knowledge_idx", "knowledge_id", "chunk_index", unique=True),
    )

    knowledge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_knowledge.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # P2-file: project docs share this chunk table for RAG indexing.
    project_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_docs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 512 = BAAI/bge-small-zh-v1.5 output dim. See migration 0057 for the
    # rationale on keeping the dim explicit.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(512) if _PGVECTOR_AVAILABLE else JSONB,
        nullable=True,
    )
