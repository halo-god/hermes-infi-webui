"""Auth endpoints: login / refresh / logout / me / providers."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models.identity import IdentityProvider
from app.db.models.user import User
from app.deps import get_current_user
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    ProviderInfo,
    RefreshRequest,
    TokenPair,
)
from app.config import settings
from app.core import metrics, ratelimit
from app.auth_providers.base import ProviderError
from app.schemas.user import UserOut
from app.services import audit_service, auth_service

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> LoginResponse:
    ip = request.client.host if request.client else None
    who = str(req.username or "")

    # Per-IP brute-force guard — fixed window, fail-open if Redis is down.
    if ip:
        try:
            allowed, _ = await ratelimit.hit(
                f"rl:login:{ip}", settings.login_rate_limit_per_min, 60
            )
        except Exception:  # noqa: BLE001
            allowed = True
        if not allowed:
            metrics.LOGINS.labels("fail").inc()
            raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")

    try:
        user = await auth_service.authenticate(db, req)
    except HTTPException as exc:
        metrics.LOGINS.labels("fail").inc()
        await audit_service.record(
            action="auth.login", actor_name=who, target=who, ip=ip,
            result="fail", meta={"reason": exc.detail}, db=db,
        )
        await db.commit()
        raise
    metrics.LOGINS.labels("ok").inc()
    tokens = auth_service.issue_tokens(user)
    await audit_service.record(
        action="auth.login", actor_id=user.id, actor_name=user.name,
        ip=ip, result="ok", db=db,
    )
    await db.commit()
    return LoginResponse(**tokens.model_dump(), user=UserOut.model_validate(user))


@router.post("/refresh", response_model=TokenPair)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    _, tokens = await auth_service.refresh_tokens(db, req.refresh_token)
    return tokens


@router.post("/logout", status_code=204)
async def logout(req: RefreshRequest | None = None) -> Response:
    await auth_service.logout(req.refresh_token if req else None)
    return Response(status_code=204)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/change-password", status_code=204)
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    from app.core.security import verify_password, hash_password

    if not user.password_hash or not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    user.password_hash = hash_password(req.new_password)
    await db.commit()
    return Response(status_code=204)


@router.get("/providers", response_model=list[ProviderInfo])
async def providers(db: AsyncSession = Depends(get_db)) -> list[ProviderInfo]:
    """Drives the login page tabs; reflects which providers admins enabled."""
    from app.services import identity_service

    rows = await identity_service.list_providers(db)
    out = [ProviderInfo(id="local", label="账号密码", enabled=True, kind="local")]
    out += [
        ProviderInfo(id=p.id, label=p.label, enabled=p.enabled, kind=p.id) for p in rows
    ]
    return out


# ── WeCom OAuth ──────────────────────────────────────────────────────

@router.get("/wecom/authorize")
async def wecom_authorize(db: AsyncSession = Depends(get_db)):
    """Return the WeCom OAuth authorize URL for frontend redirect."""
    from app.services import identity_service
    from app.auth_providers.wecom import build_authorize_url

    provider = await db.get(IdentityProvider, "wecom")
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=403, detail="企业微信登录未启用")

    cfg = provider.config or {}
    corp_id = (cfg.get("corp_id") or "").strip()
    agent_id = (cfg.get("agent_id") or "").strip()
    redirect_uri = (cfg.get("redirect_uri") or "").strip()
    if not corp_id or not agent_id or not redirect_uri:
        raise HTTPException(status_code=500, detail="企业微信 OAuth 参数未完整配置")

    url = build_authorize_url(corp_id, agent_id, redirect_uri)
    return {"authorize_url": url}


@router.get("/wecom/silent")
async def wecom_silent(db: AsyncSession = Depends(get_db)):
    """Silent login from WeCom workbench. Redirects to OAuth2 authorize (scope=snsapi_base).

    Configure this URL as the app's homepage in WeCom admin.
    WeCom will auto-redirect with code — no QR scan needed.
    """
    from app.auth_providers.wecom import build_silent_authorize_url

    provider = await db.get(IdentityProvider, "wecom")
    if provider is None or not provider.enabled:
        return Response(
            content="<html><body><h3>企业微信登录未启用</h3><p>请联系管理员。</p></body></html>",
            media_type="text/html; charset=utf-8",
            status_code=403,
        )

    cfg = provider.config or {}
    corp_id = (cfg.get("corp_id") or "").strip()
    agent_id = (cfg.get("agent_id") or "").strip()
    # Silent flow uses silent_redirect_uri if configured, falls back to redirect_uri
    silent_redirect_uri = (cfg.get("silent_redirect_uri") or cfg.get("redirect_uri") or "").strip()
    if not corp_id or not agent_id or not silent_redirect_uri:
        return Response(
            content="<html><body><h3>企业微信免登未完整配置</h3><p>请联系管理员配置回调地址。</p></body></html>",
            media_type="text/html; charset=utf-8",
            status_code=500,
        )

    url = build_silent_authorize_url(corp_id, agent_id, silent_redirect_uri)
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=url, status_code=302)


@router.get("/wecom/callback")
async def wecom_callback(code: str = "", state: str = "", db: AsyncSession = Depends(get_db)):
    """WeCom OAuth callback. Exchanges code for user identity, provisions, returns HTML with tokens."""
    from app.services import identity_service
    from app.auth_providers.wecom import authenticate as wecom_authenticate

    if not code:
        raise HTTPException(status_code=400, detail="缺少授权码")

    provider = await db.get(IdentityProvider, "wecom")
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=403, detail="企业微信登录未启用")

    try:
        info = await wecom_authenticate(provider.config, code)
    except ProviderError as e:
        # Return HTML with error message
        return _wecom_callback_html(error=str(e))

    # Provision user (create/update + team mapping)
    mappings = await identity_service.list_mappings(db, "wecom")
    user = await identity_service.provision_user(db, info, mappings)

    # Issue JWT tokens
    tokens = auth_service.issue_tokens(user)

    return _wecom_callback_html(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
    )


def _wecom_callback_html(
    access_token: str = "",
    refresh_token: str = "",
    error: str = "",
) -> Response:
    """Return an HTML page that posts tokens to the opener window and closes itself."""
    if error:
        import html as html_mod
        safe_error = html_mod.escape(error)
        content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>企业微信登录</title></head>
<body>
<div id="msg" style="text-align:center;padding:40px;font-family:sans-serif;color:#c00;">
  登录失败：{safe_error}
</div>
<script>
  window.location.href = '/login#error=' + encodeURIComponent('{safe_error}');
</script>
</body></html>"""
    else:
        content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>企业微信登录</title></head>
<body>
<div style="text-align:center;padding:40px;font-family:sans-serif;color:#3a8a7a;">
  登录成功，正在跳转...
</div>
<script>
  if (window.opener) {{
    window.opener.postMessage({{
      type: 'wecom-callback',
      access_token: '{access_token}',
      refresh_token: '{refresh_token}'
    }}, '*');
    setTimeout(function() {{ window.close(); }}, 500);
  }} else {{
    // Workbench / no-opener fallback: pass tokens via URL hash
    window.location.href = '/login#access_token={access_token}&refresh_token={refresh_token}';
  }}
</script>
</body></html>"""

    return Response(content=content, media_type="text/html; charset=utf-8")
