"""Agent execution trace: fine-grained per-step token/cost tracking.

Records each tool_call / thought / plan event within a turn, enabling
"which step was the most expensive" analysis in the admin usage dashboard.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class AgentTrace(UUIDPrimaryKey, Timestamps, Base):
    """One row per step within a turn (tool_call, thought, plan, etc.).

    Enables fine-grained cost analysis: "this tool_call cost 5000 tokens
    and took 2.3s" rather than just "this message used 20k tokens total".
    """
    __tablename__ = "agent_traces"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True,
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # tool_call|thought|plan|response
    title: Mapped[str] = mapped_column(Text, default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class Artifact(UUIDPrimaryKey, Timestamps, Base):
    """An executable artifact extracted from an AI response.

    When the AI response contains code blocks (```sql, ```python, etc.),
    they are automatically extracted as Artifacts that the user can
    execute with one click. The execution result is stored back.
    """
    __tablename__ = "artifacts"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(16), nullable=False)  # sql|python|javascript|json|shell
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)  # draft|executed|failed
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
