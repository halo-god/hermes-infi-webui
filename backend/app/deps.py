"""Shared FastAPI dependencies."""
from __future__ import annotations

import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_core
from app.core.metrics import AUTH_FAILURES
from app.core.security import decode_token
from app.db.base import get_db
from app.db.models.user import User
from app.services import user_service

_bearer = HTTPBearer(auto_error=False)


def _reject(reason: str, detail: str) -> HTTPException:
    AUTH_FAILURES.labels(reason).inc()
    return HTTPException(status_code=401, detail=detail)


async def user_from_access_token(token: str, db: AsyncSession) -> User:
    """Resolve a User from a raw access token. Raises 401 on any failure."""
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise _reject("invalid", "令牌无效或已过期")

    if payload.get("type") != "access":
        raise _reject("wrong_type", "令牌类型错误")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise _reject("bad_subject", "令牌主体无效")

    # Revocation check (one Redis round-trip): a logged-out token is blacklisted
    # by jti; a password change / forced logout bumps the per-user watermark so
    # every token issued before it is rejected — closes the window where an
    # access token would otherwise stay valid until its natural exp.
    jti = payload.get("jti")
    blacklisted, revoke_before = await redis_core.token_revocation_state(jti, str(user_id))
    if blacklisted:
        raise _reject("revoked", "令牌已失效")
    if revoke_before and int(payload.get("iat", 0)) < revoke_before:
        raise _reject("revoked", "登录状态已失效，请重新登录")

    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise _reject("inactive", "用户不存在或已停用")
    return user


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await user_from_access_token(creds.credentials, db)


async def user_from_media_ticket(ticket: str | None, db: AsyncSession) -> User:
    """Resolve a User from a short-lived media ticket (SSE/WS/file-raw URLs).

    Tickets carry no API authority and expire fast — used where the browser
    can't send an Authorization header.
    """
    user_id = await redis_core.resolve_media_ticket(ticket)
    if not user_id:
        raise _reject("bad_ticket", "票据无效或已过期")
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise _reject("bad_ticket", "票据无效或已过期")
    user = await user_service.get_by_id(db, uid)
    if user is None or not user.is_active:
        raise _reject("inactive", "用户不存在或已停用")
    return user


async def user_from_ticket_or_header(
    ticket: str | None, request, db: AsyncSession
) -> User:
    """Auth for file-raw downloads: prefer a media ticket (URL), fall back to a
    Bearer header (programmatic callers). Never accepts a raw access token in the
    query string."""
    if ticket:
        return await user_from_media_ticket(ticket, db)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return await user_from_access_token(auth_header[len("Bearer "):], db)
    raise HTTPException(status_code=401, detail="未认证")
