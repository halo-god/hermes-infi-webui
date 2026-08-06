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
os.environ.setdefault(
    "REDIS_URL", "redis://:InfiLed%40Hermes_redis@127.0.0.1:1979/0"
)

# Tests assume the 'db' storage backend (small files inline in Postgres);
# minio-offload tests switch to moto themselves. The repo's backend/.env sets
# STORAGE_BACKEND=minio for production — override it so it can't leak in
# (env vars take precedence over .env in pydantic-settings).
os.environ["STORAGE_BACKEND"] = "db"

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
def _reset_redis_client():
    """Drop the cached async Redis client around every test.

    asyncio_mode=auto gives each test its own event loop; a client created in a
    prior loop raises "attached to a different loop" when reused. Resetting the
    module global forces a fresh, correctly-bound client per test.
    """
    import app.core.redis as _redis_mod
    _redis_mod._client = None
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
    from app.core.security import hash_password
    user = User(
        id=uuid.uuid4(),
        email="admin@hermes.io",
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
