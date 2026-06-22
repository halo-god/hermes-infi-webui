"""Single-row tenant system settings (branding, model gateway, quotas)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEFAULT_SETTINGS = {
    "branding": {
        "tenant_name": "Hermes Internal",
        "display": "Hermes — 信使",
        "short_name": "Hermes",
        "login_tagline": "凡所欲遣，皆可托信使。",
        "login_subtitle": "连接你的 Hermes Agent，开始协作。",
        "accent": "#b8852a",
    },
    "model_gateway": {
        "default_model": "hermes-4",
        "monthly_token_quota": 12000000,
        "rate_limit_per_min": 30,
        "overage": "soft",  # soft | hard | warn
    },
}


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    data: Mapped[dict] = mapped_column(JSONB, default=lambda: DEFAULT_SETTINGS)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
