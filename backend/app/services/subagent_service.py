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
    first. Unread is computed per-row against BackgroundSubagent.last_read_at."""
    stmt = (
        select(BackgroundSubagent)
        .where(BackgroundSubagent.parent_conversation_id == parent_conversation_id)
        .order_by(BackgroundSubagent.created_at.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return []

    # Batch-compute unread counts in a single grouped query instead of N
    # per-row round-trips. We fetch total agent messages per sub-conversation,
    # then subtract the "read" portion (messages up to last_read_at) per row.
    sub_conv_ids = [r.subagent_conversation_id for r in rows]
    total_rows = (
        await db.execute(
            select(Message.conversation_id, func.count(Message.id))
            .where(
                Message.conversation_id.in_(sub_conv_ids),
                Message.role == "agent",
            )
            .group_by(Message.conversation_id)
        )
    ).all()
    total_by_conv = {row[0]: int(row[1] or 0) for row in total_rows}

    # Fetch read counts only for rows that have a last_read_at cutoff.
    read_cutoffs = {r.subagent_conversation_id: r.last_read_at for r in rows if r.last_read_at}
    read_by_conv: dict[uuid.UUID, int] = {}
    if read_cutoffs:
        # Build OR conditions for each (conversation_id, last_read_at) pair.
        from sqlalchemy import or_, and_
        conditions = [
            and_(Message.conversation_id == cid, Message.created_at <= cutoff)
            for cid, cutoff in read_cutoffs.items()
        ]
        read_rows = (
            await db.execute(
                select(Message.conversation_id, func.count(Message.id))
                .where(Message.role == "agent", or_(*conditions))
                .group_by(Message.conversation_id)
            )
        ).all()
        read_by_conv = {row[0]: int(row[1] or 0) for row in read_rows}

    out: list[tuple[BackgroundSubagent, int]] = []
    for row in rows:
        total = total_by_conv.get(row.subagent_conversation_id, 0)
        read = read_by_conv.get(row.subagent_conversation_id, 0) if row.last_read_at else 0
        out.append((row, max(0, total - read)))
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
