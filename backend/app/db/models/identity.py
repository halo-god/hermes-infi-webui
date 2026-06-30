"""Identity providers (LDAP/AD, WeCom, …) + department→team mapping."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import Timestamps, UUIDPrimaryKey


class IdentityProvider(Timestamps, Base):
    __tablename__ = "identity_providers"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)  # ldap|wecom|saml|oidc|feishu
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)


class DeptTeamMapping(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "dept_team_mappings"

    provider_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("identity_providers.id", ondelete="CASCADE"), index=True
    )
    # For multi-org providers (WeCom): scopes the mapping to one configured org.
    # NULL = applies to the legacy/default org. Other providers (LDAP) leave it NULL.
    org_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    match_basis: Mapped[str] = mapped_column(String(16), default="attribute")  # dn | attribute
    source_value: Mapped[str] = mapped_column(String(255), nullable=False)
    dept: Mapped[str | None] = mapped_column(String(120))
    default_role: Mapped[str] = mapped_column(String(16), default="member")
    auto_join_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
