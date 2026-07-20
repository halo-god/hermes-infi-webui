"""WeCom (企业微信) OAuth2 provider.

Flow:
  1. Frontend redirects user to WeCom authorize URL (build_authorize_url)
  2. User scans QR -> WeCom redirects to callback with ?code=xxx
  3. Backend exchanges code -> access_token -> user info -> department
  4. identity_service.provision_user() creates/updates user + team mapping
"""
from __future__ import annotations

import logging

import httpx
from app.auth_providers.base import IdentityInfo, ProviderError

logger = logging.getLogger(__name__)

_WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
_WECOM_USERINFO_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo"
_WECOM_USER_DETAIL_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/get"
_WECOM_DEPT_URL = "https://qyapi.weixin.qq.com/cgi-bin/department/list"

_TIMEOUT = 10
# access_token cache TTL buffer: WeCom tokens last 7200s; we cache slightly
# shorter to avoid edge-case expiry. Keyed by corp_id to support multi-org.
_TOKEN_CACHE_TTL = 7000

# The implicit org synthesized from a legacy single-org config (top-level
# corp_id/agent_id/... without an `orgs` list). It keeps the bare-userid
# external_id fallback so users provisioned before multi-org still resolve.
DEFAULT_ORG_ID = "default"

# State sentinels emitted by older single-org authorize links.
_STATE_SENTINELS = {"", "wecom", "wecom_silent"}

_ORG_FIELDS = ("corp_id", "agent_id", "app_secret", "redirect_uri", "silent_redirect_uri")


def normalize_orgs(config: dict | None) -> list[dict]:
    """Return the configured WeCom orgs as a normalized list.

    Supports both the new `config.orgs = [...]` shape and the legacy single
    top-level config (treated as one implicit org with id ``default``).
    """
    config = config or {}
    raw = config.get("orgs")
    out: list[dict] = []
    if isinstance(raw, list) and raw:
        for i, o in enumerate(raw):
            if not isinstance(o, dict):
                continue
            oid = (o.get("id") or "").strip() or (DEFAULT_ORG_ID if i == 0 else f"org{i}")
            out.append({
                "id": oid,
                "name": (o.get("name") or "").strip() or "企业微信",
                **{k: (o.get(k) or "").strip() for k in _ORG_FIELDS},
            })
        return out
    # Legacy single-org config
    if (config.get("corp_id") or "").strip():
        out.append({
            "id": DEFAULT_ORG_ID,
            "name": (config.get("name") or "").strip() or "企业微信",
            **{k: (config.get(k) or "").strip() for k in _ORG_FIELDS},
        })
    return out


def get_org(config: dict | None, org_id: str | None) -> dict | None:
    """Resolve a single org by id. Falls back to the first org for empty/legacy
    state sentinels; returns None for an unknown explicit id."""
    orgs = normalize_orgs(config)
    if not orgs:
        return None
    if org_id and org_id not in _STATE_SENTINELS:
        for o in orgs:
            if o["id"] == org_id:
                return o
        return None
    return orgs[0]


def build_authorize_url(corp_id: str, agent_id: str, redirect_uri: str, state: str = "") -> str:
    """Build the WeCom OAuth authorize URL for QR scan login."""
    import urllib.parse
    params = {
        "appid": corp_id,
        "agentid": agent_id,
        "redirect_uri": redirect_uri,
        "state": state or "wecom",
    }
    return f"https://open.work.weixin.qq.com/wwopen/sso/qrConnect?{urllib.parse.urlencode(params)}"


def build_silent_authorize_url(corp_id: str, agent_id: str, redirect_uri: str, state: str = "") -> str:
    """Build the WeCom OAuth2 authorize URL for silent (workbench) login.

    Uses scope=snsapi_base so the user doesn't need to confirm —
    WeCom auto-redirects with code if the user is already authenticated.
    """
    import urllib.parse
    params = {
        "appid": corp_id,
        "agentid": agent_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "snsapi_base",
        "state": state or "wecom_silent",
    }
    return f"https://open.work.weixin.qq.com/wwopen/sso/oauth2/authorize?{urllib.parse.urlencode(params)}#wechat_redirect"


async def _get_access_token(corp_id: str, app_secret: str) -> str:
    """Get WeCom access_token with Redis caching to avoid rate-limit hits.

    WeCom access_tokens are valid for 7200s and have API call frequency limits;
    caching per corp_id prevents redundant gettoken calls on every login."""
    from app.core import redis as redis_core

    cache_key = f"wecom:access_token:{corp_id}"
    try:
        cached = await redis_core.cache_get(cache_key)
        if cached:
            return cached
    except Exception:  # noqa: BLE001
        logger.debug("Redis unavailable for WeCom token cache, falling through to API")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(_WECOM_TOKEN_URL, params={
            "corpid": corp_id, "corpsecret": app_secret,
        })
        data = r.json()
        if data.get("errcode", 0) != 0:
            raise ProviderError(f"获取 access_token 失败: {data.get('errmsg', '未知错误')}")
        token = data["access_token"]
        expires_in = data.get("expires_in", _TOKEN_CACHE_TTL)

    try:
        await redis_core.cache_set(cache_key, token, min(expires_in - 200, _TOKEN_CACHE_TTL))
    except Exception:  # noqa: BLE001
        logger.debug("Redis unavailable for WeCom token cache write")

    return token


async def authenticate(config: dict, code: str) -> IdentityInfo:
    """Exchange OAuth code for user identity.

    Steps:
      1. code -> access_token (via gettoken with corp_id + secret, cached)
      2. access_token + code -> userid (via user/getuserinfo)
      3. userid -> user detail (via user/get) - includes department[]
      4. department[0] -> department name (via department/list)
    """
    corp_id = (config.get("corp_id") or "").strip()
    app_secret = (config.get("app_secret") or "").strip()
    if not corp_id or not app_secret:
        raise ProviderError("企业微信未配置 corp_id 或 app_secret")

    try:
        access_token = await _get_access_token(corp_id, app_secret)
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"获取 access_token 网络异常: {exc}") from exc

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # Step 2: Get userid from code
        try:
            r = await client.get(_WECOM_USERINFO_URL, params={
                "access_token": access_token, "code": code,
            })
            data = r.json()
        except Exception as exc:
            raise ProviderError(f"获取用户信息网络异常: {exc}") from exc
        errcode = data.get("errcode", 0)
        if errcode != 0:
            # errcode 42003 = code expired; 40029 = invalid code
            raise ProviderError(f"获取用户信息失败: {data.get('errmsg', f'errcode={errcode}')}")
        # WeCom returns "UserId" (capital U) in getuserinfo; "openid" for external contacts.
        userid = data.get("UserId") or data.get("userid")
        openid = data.get("openid")
        if not userid:
            if openid:
                raise ProviderError(
                    "企业微信未返回用户 ID（仅返回 openid），该用户可能不在应用的可见范围内。"
                    "请管理员在企业微信后台 -> 应用管理 -> 该应用 -> 可见范围 中添加此用户。"
                )
            raise ProviderError(f"企业微信未返回用户 ID，可能用户未授权。原始响应: {data}")

        # Step 3: Get user detail
        try:
            r = await client.get(_WECOM_USER_DETAIL_URL, params={
                "access_token": access_token, "userid": userid,
            })
            data = r.json()
        except Exception as exc:
            raise ProviderError(f"获取用户详情网络异常: {exc}") from exc
        if data.get("errcode", 0) != 0:
            raise ProviderError(f"获取用户详情失败: {data.get('errmsg', '未知错误')}")

        name = data.get("name", userid)
        email = data.get("email", "")
        if not email:
            # WeCom may return empty email; generate a placeholder namespaced by
            # corp so the same userid across two orgs never collides on email.
            email = f"{userid}@{corp_id}.wecom.infiled.com"
        department_ids = data.get("department", [])  # list of int dept IDs

        # Step 4: Resolve department name from first department ID
        dept_name = None
        if department_ids:
            dept_name = await _resolve_department(client, access_token, department_ids[0])

    return IdentityInfo(
        # Namespace by corp_id: a WeCom userid is only unique within one corp,
        # so multi-org deployments must not let corp-B's "zhangsan" land on
        # corp-A's account.
        external_id=f"{corp_id}:{userid}",
        email=email.lower(),
        name=name,
        source="wecom",
        department=dept_name,
        groups=[],  # WeCom doesn't have LDAP-style groups
    )


async def _resolve_department(client: httpx.AsyncClient, access_token: str, dept_id: int) -> str | None:
    """Resolve a WeCom department ID to its name."""
    try:
        r = await client.get(_WECOM_DEPT_URL, params={
            "access_token": access_token, "id": dept_id,
        })
        data = r.json()
        if data.get("errcode", 0) == 0:
            departments = data.get("department", [])
            if departments:
                return departments[0].get("name")
    except Exception:  # noqa: BLE001
        pass
    return None
