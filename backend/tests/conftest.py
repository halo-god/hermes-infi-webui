"""Shared test fixtures.

Overrides the app database engine before import.
Per-test isolation via transaction rollback.
Requires: docker compose up -d (postgres on port 5432)
"""
from __future__ import annotations

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Set DATABASE_URL BEFORE any app import ──
# Honor a caller/CI-provided DATABASE_URL (CI's Postgres uses different creds);
# only fall back to the local docker-compose default when it is unset. Use
# 127.0.0.1 (not localhost) so tests don't depend on the host resolver.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://hermes:hermes@127.0.0.1:5432/hermes_test"
)
# Same for Redis: pin to the loopback IP so an intermittent mDNS resolver
# stall can't hang the suite's connections.
# WARNING: production hermes-redis listens on 127.0.0.1:1979 (docker compose map).
# Previously this setdefault pointed at 1979, so running pytest locally without an
# explicit REDIS_URL hit PRODUCTION Redis: real ACP tasks were dispatched to the
# production acp_stream (78x, verified 2026-08-11) creating shell roundtable sessions
# and burning API, and _reset_redis_client deleted production rl:* rate-limit keys.
# Tests MUST use a dedicated instance; start one before running:
#   docker run -d --name hermes-test-redis -p 127.0.0.1:6380:6379 redis:7-alpine
# CI (.github/workflows/ci.yml) sets REDIS_URL=redis://localhost:6379 explicitly, so
# this default only affects local runs.
os.environ.setdefault(
    "REDIS_URL", "redis://127.0.0.1:6380/0"
)
# ── Hard guard: NEVER let the suite touch the production Redis ──
# Belt-and-suspenders on top of the default above: if anything (a caller
# exporting REDIS_URL, a future edit, a stale env) points the suite at the
# production instance (127.0.0.1:1979, db0), fail loudly at import instead of
# silently enqueueing real ACP tasks / deleting production rate-limit keys.
from urllib.parse import urlparse as _urlparse
# Whitelist semantics, NOT a blocklist: any host:port that is not a
# dedicated test instance is rejected. The old guard only blocked
# localhost/127.0.0.1:1979, so container-name variants (redis:1979,
# hermes-redis:1979) or in-network port 6379 wrote straight through to
# the production instance. Approved targets:
_ALLOWED_REDIS = (
    ("127.0.0.1", 6380),  # local dedicated instance (hermes-test-redis)
    ("localhost", 6380),  # same, hostname alias
    ("localhost", 6379),  # CI (.github/workflows/ci.yml sets this explicitly)
    ("127.0.0.1", 6379),  # CI variant
)


def _assert_approved_redis(label: str, raw_url: str) -> None:
    """Fail loudly if the URL the suite would actually use is unapproved.

    CRITICAL: validate the pydantic settings singleton (settings.redis_url),
    NOT just os.environ. app/core/redis.py connects via settings.redis_url
    (redis.py:14-15). The singleton is lru_cache'd at first app import; if it
    was instantiated BEFORE this conftest's setdefault ran (e.g. by a plugin
    or a script importing app modules directly), it holds the backend/.env
    production value (127.0.0.1:1979) even though os.environ was patched to
    6380. Checking os.environ alone passes while the suite still hits
    production Redis — the 2026-08-12 recurrence.
    """
    _ru = _urlparse(raw_url)
    if (_ru.hostname, _ru.port) not in _ALLOWED_REDIS:
        raise RuntimeError(
            f"{label} points at an unapproved Redis ({_ru.hostname}:{_ru.port}). "
            "Tests MUST use the dedicated instance (127.0.0.1:6380); CI sets "
            "REDIS_URL=redis://localhost:6379 explicitly. Never run tests against "
            "the production instance (127.0.0.1:1979)."
        )


# Belt: env-level value must be approved...
_assert_approved_redis("REDIS_URL", os.environ["REDIS_URL"])
# ...and suspenders: the value the app ACTUALLY uses must be approved too.
# Importing app.config here forces the singleton to resolve NOW (after the
# setdefault above), so the guard sees whatever the suite would connect to.
from app.config import settings as _settings  # noqa: E402
_assert_approved_redis("settings.redis_url", _settings.redis_url)

# Pin storage backend BEFORE the pydantic singleton can be first constructed:
# backend/.env sets STORAGE_BACKEND=minio (production); env beats .env in
# pydantic-settings, so setting it before ANY settings import (incl. the
# redis effective-value guard below) keeps every test on the 'db' backend.
os.environ["STORAGE_BACKEND"] = "db"

# The REDIS_URL env check above is necessary but NOT sufficient (verified
# 2026-08-12): pydantic-settings resolves settings.redis_url ONCE, at
# singleton instantiation. If a parent conftest, plugin, or earlier import
# constructs the singleton before this module's setdefault ran, it reads
# backend/.env (production 127.0.0.1:1979) while os.environ here reads the
# pinned 6380, so the env guard passes while app.core.redis and
# agent_runner.acp_client still dial production (78 real ACP roundtable
# dispatches, 2026-08-04..11). Guard the EFFECTIVE value the app actually
# connects with.
from app.config import settings as _settings  # noqa: E402
_sru = _urlparse(_settings.redis_url)
if (_sru.hostname, _sru.port) not in _ALLOWED_REDIS:
    raise RuntimeError(
        f"settings.redis_url resolves to an unapproved Redis "
        f"({_sru.hostname}:{_sru.port}); the pydantic settings singleton was "
        "instantiated before conftest pinned the test instance and read "
        "backend/.env. Re-run with REDIS_URL=redis://127.0.0.1:6380/0 exported "
        "(env beats .env) and do NOT run the suite until this resolves."
    )
# ── Second guard: the app Settings singleton must ALSO target test Redis ──
# The 2026-08-12 production-dispatch incident: pytest tests/test_group_chat.py
# (test_group_roundtable_attachment_gets_content_blocks) dispatched a REAL
# roundtable session ("大家看看这份笔记" + notes.md fixture) to the production
# acp_stream because the ACP subprocess env (agent_runner/acp_client.py) and the
# test assertions (app/core/redis.py) read settings.redis_url — the pydantic
# Settings singleton — NOT os.environ["REDIS_URL"]. If that singleton is
# instantiated before the setdefault above (a higher-level conftest / plugin
# importing app.config early), it picks up backend/.env's production URL
# (127.0.0.1:1979) and the env-var guard above passes anyway. Validate the
# singleton itself; creating it here (env vars set) also pins it to 6380 for
# every subsequent import.
from app.config import get_settings as _get_settings  # noqa: E402

_sredis = _get_settings().redis_url
_sru = _urlparse(_sredis)
if (_sru.hostname, _sru.port) not in _ALLOWED_REDIS:
    raise RuntimeError(
        f"settings.redis_url points at an unapproved Redis "
        f"({_sru.hostname}:{_sru.port}). The Settings singleton must resolve to "
        "the dedicated test instance (127.0.0.1:6380) — see the 2026-08-12 "
        "production-dispatch note above. Something imported app.config before "
        "conftest pinned REDIS_URL; move that import after this file, or export "
        "REDIS_URL before pytest runs."
    )

# Tests must never depend on the network: force HF Hub offline so embedding
# model loads use the local cache only (an unreachable huggingface.co would
# otherwise stall real-model tests for 30s+ per DNS timeout).
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from app.core.security import create_token  # noqa: E402
from app.db import base as db_base  # noqa: E402
from app.db.models.user import User  # noqa: E402

TEST_DB_URL = os.environ["DATABASE_URL"]
# NullPool: each test runs in its own event loop (asyncio_mode=auto); pooled
# asyncpg connections created in one loop break when reused from another.
from sqlalchemy.pool import NullPool  # noqa: E402

test_engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)
test_session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

# Override app engine
db_base.engine = test_engine
db_base.async_session_maker = test_session_maker


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_tables():
    """Recreate all tables once (drop schema first so model changes — e.g. new
    chunk metadata columns — are reflected; alembic migrations aren't run
    here, and legacy tables outside the metadata may exist)."""
    from sqlalchemy import text as sa_text
    from app.db.base import Base
    import app.db.models  # noqa: F401 — register every model so create_all is complete
    async with test_engine.begin() as conn:
        await conn.execute(sa_text("DROP SCHEMA public CASCADE"))
        await conn.execute(sa_text("CREATE SCHEMA public"))
        # Extensions live in the schema we just dropped — recreate both
        # pgvector (RAG embeddings) and pg_trgm (episodic-memory similarity).
        await conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _ensure_admin_matches_settings(_create_tables):
    """Upsert the platform admin so its password matches the loaded settings.

    Tests log in as the admin directly (settings.first_admin_*) rather than
    through an admin_user fixture, so a stale user left in a reused test DB
    would break them. Idempotent."""
    from sqlalchemy import select
    from app.config import settings
    from app.core.security import hash_password
    async with test_session_maker() as db:
        stmt = select(User).where(User.email == settings.first_admin_email)
        user = (await db.execute(stmt)).scalar_one_or_none()
        if user is None:
            db.add(User(
                id=uuid.uuid4(),
                email=settings.first_admin_email,
                name="Admin User",
                password_hash=hash_password(settings.first_admin_password),
                is_active=True,
                role="super_admin",
            ))
        else:
            user.password_hash = hash_password(settings.first_admin_password)
            user.role = "super_admin"  # stale rows may predate the role rename
        await db.commit()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _reset_redis_client():
    """Drop the cached async Redis client around every test + clear rate-limit keys.

    asyncio_mode=auto gives each test its own event loop; a client created in a
    prior loop raises "attached to a different loop" when reused. Resetting the
    module global forces a fresh, correctly-bound client per test.
    Rate-limit counters (rl:*) live in the shared Redis db0 and now use 15-min
    windows — without clearing, login attempts from earlier tests bleed into the
    current one and spuriously trip the 429 guard.
    """
    import app.core.redis as _redis_mod
    _redis_mod._client = None
    r = _redis_mod.get_redis()
    async for k in r.scan_iter("rl:*", count=500):
        await r.delete(k)
    await r.delete("cfg:rate_limit_per_min")
    yield
    _redis_mod._client = None


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Session wrapped in a transaction that rolls back after test."""
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest_asyncio.fixture
async def client(db: AsyncSession):
    """Async test client with overridden DB."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app as real_app
    from app.db.base import get_db

    async def _override():
        yield db

    real_app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    real_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    from app.core.security import hash_password
    user = User(
        id=uuid.uuid4(),
        email="test@hermes.io",
        name="Test User",
        password_hash=hash_password("Test@1234"),
        is_active=True,
        role="member",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession) -> User:
    """The session-scoped _ensure_admin_matches_settings already seeds
    settings.first_admin_email as super_admin — reuse that row instead of
    inserting a duplicate (unique email constraint)."""
    from sqlalchemy import select

    from app.config import settings
    from app.db.models.user import User

    user = (
        await db.execute(
            select(User).where(User.email == settings.first_admin_email)
        )
    ).scalar_one_or_none()
    if user is None:
        from app.core.security import hash_password
        user = User(
            id=uuid.uuid4(),
            email=settings.first_admin_email,
            name="Admin User",
            password_hash=hash_password("Admin@1234"),
            is_active=True,
            role="admin",
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
    return user


@pytest.fixture
def user_token(test_user: User) -> str:
    token, _ = create_token(str(test_user.id), "access")
    return token


@pytest.fixture
def admin_token(admin_user: User) -> str:
    token, _ = create_token(str(admin_user.id), "access")
    return token


@pytest.fixture
def auth_headers(user_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}
