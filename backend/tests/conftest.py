"""Shared test fixtures.

Overrides the app database engine before import.
Per-test isolation via transaction rollback.
Requires: docker compose up -d (postgres on port 5432)
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Set DATABASE_URL BEFORE any app import ──
os.environ["DATABASE_URL"] = "postgresql+asyncpg://hermes:hermes@localhost:5432/hermes_test"

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
    """Create all tables once."""
    from app.db.base import Base
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


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
