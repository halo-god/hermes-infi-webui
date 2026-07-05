"""Background subagent orchestration — spawn/list/send/read/stop.

A background subagent is a headless Conversation (type="subagent") the
parent conversation spawned, paired with a BackgroundSubagent row tracking
orchestration state (status, deadlines, read cursor). The transcript is just
normal Message rows on that conversation, so existing chat-rendering code
displays it with zero changes — only the orchestration metadata here is new.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_core
from app.db.models.agent import Profile
from app.db.models.conversation import Conversation, Message
from app.db.models.subagent import BackgroundSubagent
from app.services.conversation_service import _profile_dir, _resolve_mcp_servers


async def spawn_subagent(
    db: AsyncSession,
    parent: Conversation,
    owner_id: uuid.UUID,
    *,
    purpose: str,
    initial_prompt: str,
    agent_id: str | None = None,
    profile_id: str | None = None,
) -> BackgroundSubagent:
    """Create the orchestration row + its headless Conversation, then enqueue
    the spawn task. Returns immediately — the id is known synchronously, the
    caller doesn't wait on the runner to actually start the subprocess."""
    effective_agent_id = agent_id or parent.primary_agent_id or "hermes"
    system_prompt: str | None = None
    profile_dir: str | None = None
    mcp_servers: list[dict] = []
    profile: Profile | None = None
    if profile_id:
        profile = await db.get(Profile, profile_id)
        if profile:
            effective_agent_id = agent_id or profile.default_agent_id
            system_prompt = profile.system_prompt or None
            profile_dir = _profile_dir(profile)
            mcp_servers = await _resolve_mcp_servers(db, profile)

    subconv = Conversation(
        owner_id=owner_id,
        title=(purpose[:60] or "后台任务"),
        type="subagent",
        primary_agent_id=effective_agent_id,
        profile_id=str(profile.id) if profile else None,
    )
    db.add(subconv)
    await db.flush()

    row = BackgroundSubagent(
        parent_conversation_id=parent.id,
        subagent_conversation_id=subconv.id,
        owner_id=owner_id,
        purpose=purpose,
        agent_id=effective_agent_id,
        profile_id=profile.id if profile else None,
        status="starting",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await redis_core.enqueue_prompt({
        "type": "subagent_spawn",
        "subagent_id": str(row.id),
        "agent_id": effective_agent_id,
        "system_prompt": system_prompt,
        "profile_dir": profile_dir,
        "mcp_servers": mcp_servers,
        "initial_prompt": initial_prompt,
    })
    return row


async def list_subagents(
    db: AsyncSession, parent_conversation_id: uuid.UUID,
) -> list[tuple[BackgroundSubagent, int]]:
    """Return [(row, unread_count), ...] for the parent's subagents, newest
    first. Unread is computed per-row against BackgroundSubagent.last_read_at
    (mirrors the GroupMember.last_read_at unread pattern, per-row cutoff so
    no single grouped query — the expected subagent count per conversation
    is small, this isn't a hot path)."""
    stmt = (
        select(BackgroundSubagent)
        .where(BackgroundSubagent.parent_conversation_id == parent_conversation_id)
        .order_by(BackgroundSubagent.created_at.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    out: list[tuple[BackgroundSubagent, int]] = []
    for row in rows:
        count_stmt = select(func.count(Message.id)).where(
            Message.conversation_id == row.subagent_conversation_id,
            Message.role == "agent",
        )
        if row.last_read_at:
            count_stmt = count_stmt.where(Message.created_at > row.last_read_at)
        unread = (await db.execute(count_stmt)).scalar_one()
        out.append((row, int(unread or 0)))
    return out


async def get_subagent(
    db: AsyncSession, parent_conversation_id: uuid.UUID, subagent_id: uuid.UUID,
) -> BackgroundSubagent | None:
    row = await db.get(BackgroundSubagent, subagent_id)
    if row is None or row.parent_conversation_id != parent_conversation_id:
        return None
    return row


async def send_to_subagent(row: BackgroundSubagent, text: str) -> None:
    await redis_core.enqueue_prompt({
        "type": "subagent_send", "subagent_id": str(row.id), "text": text,
    })


async def mark_subagent_read(db: AsyncSession, row: BackgroundSubagent) -> None:
    row.last_read_at = datetime.now(timezone.utc)
    await db.commit()


async def request_stop_subagent(row: BackgroundSubagent) -> None:
    await redis_core.publish_control(
        str(row.subagent_conversation_id),
        {"type": "subagent_stop", "subagent_id": str(row.id)},
    )
