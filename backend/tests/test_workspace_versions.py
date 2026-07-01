"""Workspace file version history: timezone correctness + concurrent-write race.

Uses real commits via async_session_maker (not the rollback-wrapped `db`
fixture) because the race test needs two independent DB connections/sessions
racing on the same row — a single AsyncSession can't be used concurrently,
and the `db` fixture's uncommitted outer transaction wouldn't be visible to
a second connection anyway.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text

from app.db.base import async_session_maker
from app.db.models.conversation import Conversation
from app.db.models.user import User
from app.db.models.workspace import WorkspaceFile, WorkspaceFileVersion
from app.services import conversation_service as svc


async def _services_ok() -> bool:
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_workspace_file_version_created_at_is_timezone_aware():
    if not await _services_ok():
        pytest.skip("PostgreSQL not reachable")

    async with async_session_maker() as db:
        from app.core.security import hash_password
        owner = User(
            id=uuid.uuid4(), email=f"wv-{uuid.uuid4().hex[:8]}@h.io", name="owner",
            password_hash=hash_password("Test@1234"), is_active=True, role="member",
        )
        db.add(owner)
        await db.flush()
        convo = Conversation(owner_id=owner.id, title="ws-version-test")
        db.add(convo)
        await db.flush()
        f = WorkspaceFile(
            conversation_id=convo.id, name="report.md", kind="md",
            content="v1", size_bytes=2, current_version=1,
        )
        db.add(f)
        await db.commit()
        await db.refresh(f)
        convo_id = convo.id

    try:
        async with async_session_maker() as db:
            f2 = await db.get(WorkspaceFile, f.id)
            await svc.update_file_content(db, f2, "v2")

        async with async_session_maker() as db:
            res = await db.execute(
                select(WorkspaceFileVersion).where(WorkspaceFileVersion.file_id == f.id)
            )
            versions = res.scalars().all()
            assert len(versions) == 1
            # asyncpg returns tz-aware datetimes for timestamptz columns.
            assert versions[0].created_at.tzinfo is not None
    finally:
        async with async_session_maker() as db:
            c = await db.get(Conversation, convo_id)
            if c:
                await db.delete(c)
                await db.commit()


@pytest.mark.asyncio
async def test_concurrent_file_writes_do_not_lose_updates():
    if not await _services_ok():
        pytest.skip("PostgreSQL not reachable")

    async with async_session_maker() as db:
        from app.core.security import hash_password
        owner = User(
            id=uuid.uuid4(), email=f"wv-{uuid.uuid4().hex[:8]}@h.io", name="owner",
            password_hash=hash_password("Test@1234"), is_active=True, role="member",
        )
        db.add(owner)
        await db.flush()
        convo = Conversation(owner_id=owner.id, title="ws-race-test")
        db.add(convo)
        await db.flush()
        f = WorkspaceFile(
            conversation_id=convo.id, name="race.md", kind="md",
            content="v1", size_bytes=2, current_version=1,
        )
        db.add(f)
        await db.commit()
        await db.refresh(f)
        file_id = f.id
        convo_id = convo.id

    try:
        async def _write(new_content: str):
            async with async_session_maker() as db:
                target = await db.get(WorkspaceFile, file_id)
                await svc.update_file_content(db, target, new_content)

        # Two "concurrent" writers (independent sessions/connections) racing
        # on the same file — must not lose an update or duplicate a version.
        await asyncio.gather(_write("v2"), _write("v3"))

        async with async_session_maker() as db:
            final = await db.get(WorkspaceFile, file_id)
            assert final.current_version == 3  # both increments landed

            res = await db.execute(
                select(WorkspaceFileVersion)
                .where(WorkspaceFileVersion.file_id == file_id)
                .order_by(WorkspaceFileVersion.version_num)
            )
            versions = res.scalars().all()
            assert [v.version_num for v in versions] == [1, 2]  # no duplicates
    finally:
        async with async_session_maker() as db:
            c = await db.get(Conversation, convo_id)
            if c:
                await db.delete(c)
                await db.commit()
