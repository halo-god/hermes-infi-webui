"""External-identity provisioning, department→team mapping, provider CRUD."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_providers import wecom as _wecom
from app.auth_providers.base import IdentityInfo, ProviderError
from app.auth_providers.ldap import LDAPProvider
from app.db.models.identity import DeptTeamMapping, IdentityProvider
from app.db.models.team import TeamMember
from app.db.models.user import User
from app.services.user_service import _initials

LDAP = LDAPProvider()

# Config keys that contain secrets — never auto-clear them on save
_SECRET_KEYS = {"bind_password", "app_secret"}


# ── provider / mapping CRUD ──

async def list_providers(db: AsyncSession) -> list[IdentityProvider]:
    res = await db.execute(select(IdentityProvider).order_by(IdentityProvider.id))
    return list(res.scalars().all())


async def get_provider(db: AsyncSession, pid: str) -> IdentityProvider | None:
    return await db.get(IdentityProvider, pid)


async def update_provider(
    db: AsyncSession, pid: str, *, enabled: bool | None, config: dict | None
) -> IdentityProvider:
    p = await db.get(IdentityProvider, pid)
    if p is None:
        raise HTTPException(status_code=404, detail="身份提供商不存在")
    if enabled is not None:
        p.enabled = enabled
    if config is not None:
        existing = dict(p.config or {})
        # Merge: preserve existing secrets when frontend sends empty string
        merged = dict(existing)
        for k, v in config.items():
            if k in _SECRET_KEYS and v == "" and existing.get(k):
                continue  # keep existing secret if field was cleared
            merged[k] = v
        # Per-org secret preservation for WeCom's `orgs` list: keep an org's
        # existing app_secret when the admin saved without re-entering it.
        if isinstance(merged.get("orgs"), list):
            prev_orgs = {
                o.get("id"): o
                for o in (existing.get("orgs") or [])
                if isinstance(o, dict) and o.get("id")
            }
            for o in merged["orgs"]:
                if not isinstance(o, dict):
                    continue
                prev = prev_orgs.get(o.get("id")) or {}
                if not (o.get("app_secret") or "").strip() and prev.get("app_secret"):
                    o["app_secret"] = prev["app_secret"]
        p.config = merged
    await db.commit()
    await db.refresh(p)
    return p


async def list_mappings(
    db: AsyncSession, provider_id: str, org_id: str | None = None
) -> list[DeptTeamMapping]:
    q = select(DeptTeamMapping).where(DeptTeamMapping.provider_id == provider_id)
    if org_id is not None:
        # The default/legacy org also owns rows with a NULL org_id (created
        # before per-org mappings existed).
        if org_id == _wecom.DEFAULT_ORG_ID:
            q = q.where(
                or_(DeptTeamMapping.org_id == org_id, DeptTeamMapping.org_id.is_(None))
            )
        else:
            q = q.where(DeptTeamMapping.org_id == org_id)
    res = await db.execute(q)
    return list(res.scalars().all())


async def add_mapping(db: AsyncSession, provider_id: str, data: dict) -> DeptTeamMapping:
    m = DeptTeamMapping(
        provider_id=provider_id,
        org_id=(data.get("org_id") or None),
        match_basis=data.get("match_basis", "attribute"),
        source_value=data["source_value"],
        dept=data.get("dept"),
        default_role=data.get("default_role", "member"),
        auto_join_team_id=data.get("auto_join_team_id"),
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def delete_mapping(db: AsyncSession, mapping_id: uuid.UUID) -> None:
    m = await db.get(DeptTeamMapping, mapping_id)
    if m:
        await db.delete(m)
        await db.commit()


# ── provisioning ──
def _match(info: IdentityInfo, mappings: list[DeptTeamMapping]) -> DeptTeamMapping | None:
    for m in mappings:
        if m.match_basis == "dn":
            if any(m.source_value.lower() in g.lower() for g in info.groups) or (
                m.source_value.lower() in info.external_id.lower()
            ):
                return m
        else:  # attribute (department)
            if info.department and info.department.lower() == m.source_value.lower():
                return m
    return None


async def provision_user(
    db: AsyncSession,
    info: IdentityInfo,
    mappings: list[DeptTeamMapping],
    legacy_external_id: str | None = None,
) -> User:
    # 1) by (source, external_id)
    res = await db.execute(
        select(User).where(User.source == info.source, User.external_id == info.external_id)
    )
    user = res.scalar_one_or_none()

    # 1b) legacy fallback: users provisioned before external_id was namespaced
    # by corp_id were stored under the bare userid. Only the default/legacy org
    # passes this so a new org can never hijack an existing account.
    if user is None and legacy_external_id:
        res = await db.execute(
            select(User).where(
                User.source == info.source, User.external_id == legacy_external_id
            )
        )
        user = res.scalar_one_or_none()
        if user is not None:
            user.external_id = info.external_id  # upgrade to namespaced id

    # 2) by email (link an existing local account)
    if user is None:
        res = await db.execute(select(User).where(User.email == info.email.lower()))
        user = res.scalar_one_or_none()
        if user is not None:
            user.source = info.source
            user.external_id = info.external_id

    mapping = _match(info, mappings)
    role = mapping.default_role if mapping else "member"

    if user is None:
        user = User(
            email=info.email.lower(),
            name=info.name,
            initials=_initials(info.name),
            color="#3a6da1",
            department=info.department,
            source=info.source,
            external_id=info.external_id,
            password_hash=None,  # SSO-only
            role=role,
            status="active",
        )
        db.add(user)
        await db.flush()
    else:
        if info.department:
            user.department = info.department

    user.last_active_at = datetime.now(tz=timezone.utc)

    # auto-join the mapped team
    if mapping and mapping.auto_join_team_id:
        existing = await db.get(
            TeamMember, {"team_id": mapping.auto_join_team_id, "user_id": user.id}
        )
        if existing is None:
            db.add(
                TeamMember(
                    team_id=mapping.auto_join_team_id,
                    user_id=user.id,
                    role=mapping.default_role,
                )
            )

    await db.commit()
    await db.refresh(user)
    return user


async def test_provider(db: AsyncSession, pid: str, org_id: str | None = None) -> dict:
    """Test connectivity/credentials for an identity provider.

    Returns {"ok": bool, "message": str}.
    """
    p = await db.get(IdentityProvider, pid)
    if p is None:
        return {"ok": False, "message": "提供商不存在"}

    if pid == "ldap":
        return await asyncio.to_thread(LDAP.test_connection, p.config)

    if pid == "wecom":
        org = _wecom.get_org(p.config, org_id)
        if org is None:
            return {"ok": False, "message": "未找到该企业微信组织配置"}
        return await _test_wecom(org)

    return {"ok": False, "message": f"{pid} 连接测试暂未实现"}


async def _test_wecom(config: dict) -> dict:
    corp_id = (config.get("corp_id") or "").strip()
    app_secret = (config.get("app_secret") or "").strip()
    if not corp_id or not app_secret:
        return {"ok": False, "message": "请先填写企业ID和应用密钥"}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": corp_id, "corpsecret": app_secret},
            )
        data = r.json()
        if data.get("errcode") == 0:
            return {"ok": True, "message": "企业微信凭证验证成功，access_token 获取正常"}
        errmsg = data.get("errmsg", "未知错误")
        errcode = data.get("errcode")
        return {"ok": False, "message": f"验证失败：{errmsg}（errcode={errcode}）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"请求失败：{exc}"}


async def authenticate_external(
    db: AsyncSession, method: str, username: str, password: str
) -> User:
    provider = await db.get(IdentityProvider, method)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=403, detail=f"{method} 登录未启用")

    if method == "ldap":
        try:
            info = await asyncio.to_thread(
                LDAP.authenticate, provider.config, username, password
            )
        except ProviderError as e:
            raise HTTPException(status_code=401, detail=str(e))
        except Exception:  # noqa: BLE001 — unreachable/misconfigured LDAP → 503, not 500
            raise HTTPException(status_code=503, detail="LDAP 服务暂不可用")
    else:
        # WeCom/SAML/OIDC/Feishu: scaffold present; real exchange lands per-tenant.
        raise HTTPException(status_code=501, detail=f"{method} 登录尚未配置")

    mappings = await list_mappings(db, method)
    return await provision_user(db, info, mappings)
