"""Authentication flows: local login, token issuance, refresh, logout.

LDAP/AD and WeCom are stubbed with a clear NotImplemented path (P5).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import redis as redis_core
from app.core.exceptions import (
    InvalidCredentialsError,
    InvalidTokenError,
    TokenRevokedError,
    AccountDisabledError,
    ValidationError,
)
from app.core.security import (
    create_token,
    decode_token,
    needs_rehash,
    verify_password,
    hash_password,
)
from app.db.models.user import User
from app.schemas.auth import LoginRequest, TokenPair
from app.services import user_service

logger = logging.getLogger(__name__)


async def authenticate_local(db: AsyncSession, username: str, password: str) -> User:
    user = await user_service.get_by_email(db, username)
    if not user or not user.password_hash:
        logger.warning("Auth failed: user not found or no password set (email=%s)", username[:50])
        raise InvalidCredentialsError()
    if not verify_password(password, user.password_hash):
        logger.warning("Auth failed: invalid password (email=%s)", username[:50])
        raise InvalidCredentialsError()
    if not user.is_active or user.status == "inactive":
        logger.warning("Auth failed: account disabled (email=%s)", username[:50])
        raise AccountDisabledError()

    # Transparent rehash on algorithm/param upgrade.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.last_active_at = datetime.now(tz=timezone.utc)
    await db.commit()
    return user


async def authenticate(db: AsyncSession, req: LoginRequest) -> User:
    if req.method == "local":
        if not req.username or not req.password:
            raise ValidationError("请输入账号与密码")
        return await authenticate_local(db, str(req.username), req.password)

    # External identity providers (LDAP/AD now; WeCom/SAML/OIDC scaffolded).
    from app.services import identity_service

    if not req.username or not req.password:
        raise ValidationError("请输入账号与密码")
    return await identity_service.authenticate_external(
        db, req.method, str(req.username), req.password
    )


def issue_tokens(user: User) -> TokenPair:
    access, _ = create_token(
        str(user.id), "access", extra={"role": user.role, "name": user.name}
    )
    refresh, _ = create_token(str(user.id), "refresh")
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


async def refresh_tokens(db: AsyncSession, refresh_token: str) -> tuple[User, TokenPair]:
    try:
        payload = decode_token(refresh_token)
    except jwt.PyJWTError:
        raise InvalidTokenError("刷新令牌无效")

    if payload.get("type") != "refresh":
        raise InvalidTokenError("令牌类型错误")
    jti = payload.get("jti")
    sub = str(payload["sub"])
    blacklisted, revoke_before = await redis_core.token_revocation_state(jti, sub)
    if blacklisted:
        raise TokenRevokedError("令牌已失效")
    # Honour the per-user watermark so a password change / forced logout
    # invalidates refresh tokens on every other device too.
    if revoke_before and int(payload.get("iat", 0)) < revoke_before:
        raise TokenRevokedError("登录状态已失效，请重新登录")

    user = await user_service.get_by_id(db, _uuid(sub))
    if not user or not user.is_active:
        raise InvalidTokenError("用户不存在或已停用")

    # Rotate: blacklist the consumed refresh jti until its natural expiry.
    if jti:
        ttl = max(int(payload["exp"]) - int(datetime.now(tz=timezone.utc).timestamp()), 1)
        await redis_core.blacklist_jti(jti, ttl)

    return user, issue_tokens(user)


async def logout(refresh_token: str | None, access_token: str | None = None) -> None:
    """Revoke the session: blacklist both the refresh and the (still-valid)
    access token so a logged-out token can't be replayed until its exp."""
    now = int(datetime.now(tz=timezone.utc).timestamp())
    for tok in (refresh_token, access_token):
        if not tok:
            continue
        try:
            payload = decode_token(tok)
        except jwt.PyJWTError:
            continue
        jti = payload.get("jti")
        if jti:
            ttl = max(int(payload.get("exp", now)) - now, 1)
            await redis_core.blacklist_jti(jti, ttl)


async def revoke_all_user_tokens(user_id: str) -> None:
    """Invalidate every outstanding token for a user (password change / forced
    logout). Bumps the per-user watermark; tokens issued before now are rejected.

    TTL outlives the longest-lived token so the watermark can't lapse early.
    """
    now = int(datetime.now(tz=timezone.utc).timestamp())
    ttl = settings.refresh_token_ttl_days * 86_400 + 60
    await redis_core.set_user_revoke_before(str(user_id), now, ttl)


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)
