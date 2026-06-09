"""WeCom (企业微信) OAuth2 provider.

Flow:
  1. Frontend redirects user to WeCom authorize URL (build_authorize_url)
  2. User scans QR → WeCom redirects to callback with ?code=xxx
  3. Backend exchanges code → access_token → user info → department
  4. identity_service.provision_user() creates/updates user + team mapping
"""
from __future__ import annotations

import httpx
from app.auth_providers.base import IdentityInfo, ProviderError

_WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
_WECOM_USERINFO_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo"
_WECOM_USER_DETAIL_URL = "https://qyapi.weixin.qq.com/cgi-bin/user/get"
_WECOM_DEPT_URL = "https://qyapi.weixin.qq.com/cgi-bin/department/list"

_TIMEOUT = 10


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


async def authenticate(config: dict, code: str) -> IdentityInfo:
    """Exchange OAuth code for user identity.

    Steps:
      1. code → access_token (via gettoken with corp_id + secret)
      2. access_token + code → userid (via user/getuserinfo)
      3. userid → user detail (via user/get) — includes department[]
      4. department[0] → department name (via department/list)
    """
    corp_id = (config.get("corp_id") or "").strip()
    app_secret = (config.get("app_secret") or "").strip()
    if not corp_id or not app_secret:
        raise ProviderError("企业微信未配置 corp_id 或 app_secret")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # Step 1: Get access_token
        r = await client.get(_WECOM_TOKEN_URL, params={
            "corpid": corp_id, "corpsecret": app_secret,
        })
        data = r.json()
        if data.get("errcode", 0) != 0:
            raise ProviderError(f"获取 access_token 失败: {data.get('errmsg', '未知错误')}")
        access_token = data["access_token"]

        # Step 2: Get userid from code
        r = await client.get(_WECOM_USERINFO_URL, params={
            "access_token": access_token, "code": code,
        })
        data = r.json()
        errcode = data.get("errcode", 0)
        if errcode != 0:
            # errcode 42003 = code expired; 40029 = invalid code
            raise ProviderError(f"获取用户信息失败: {data.get('errmsg', f'errcode={errcode}')}")
        userid = data.get("userid")
        if not userid:
            raise ProviderError("企业微信未返回用户 ID，可能用户未授权")

        # Step 3: Get user detail
        r = await client.get(_WECOM_USER_DETAIL_URL, params={
            "access_token": access_token, "userid": userid,
        })
        data = r.json()
        if data.get("errcode", 0) != 0:
            raise ProviderError(f"获取用户详情失败: {data.get('errmsg', '未知错误')}")

        name = data.get("name", userid)
        email = data.get("email", "")
        if not email:
            # WeCom may return empty email; generate placeholder
            email = f"{userid}@wecom.local"
        mobile = data.get("mobile", "")
        department_ids = data.get("department", [])  # list of int dept IDs

        # Step 4: Resolve department name from first department ID
        dept_name = None
        if department_ids:
            dept_name = await _resolve_department(client, access_token, department_ids[0])

    return IdentityInfo(
        external_id=userid,
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
