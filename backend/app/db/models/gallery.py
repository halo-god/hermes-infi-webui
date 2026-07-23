"""Gallery: public marketplace for sharing digital employees, SOPs, knowledge, and tools.

Admins publish resources to the gallery; all users can browse and copy
resources to their own workspace. This mirrors StaffDeck's "广场" concept
where organizational assets are shared and reused.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class GalleryItem(UUIDPrimaryKey, Timestamps, Base):
    """A published resource in the public gallery/marketplace.

    type: profile | sop | knowledge | tool
    item_id: the original resource's ID (profiles.id, sop_skills.id, etc.)
    snapshot_json: a portable snapshot of the resource's configuration
    (for profiles: all Profile fields; for SOPs: nodes/edges/etc.)
    """
    __tablename__ = "gallery_items"

    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # profile|sop|knowledge|tool
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)  # original resource ID
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(40), default="sparkle")
    color: Mapped[str] = mapped_column(String(16), default="#b8852a")
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)  # user-defined category tag
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    published_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
