"""Uploaded branding binary assets (favicon, logo).

Kept in a separate table from ``system_settings.branding`` (which holds only
text fields) so the whole-document ``PUT /admin/settings`` cannot clobber
binary bytes, and so the public asset endpoint can stream raw bytes cheaply.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: The asset kinds we know how to store and serve.
ASSET_KINDS = ("favicon", "logo")


class BrandAsset(Base):
    __tablename__ = "branding_assets"

    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    mime: Mapped[str] = mapped_column(String(80), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
