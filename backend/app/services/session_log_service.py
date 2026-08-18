"""Admin 会话日志 (session log) querying.

Builds conversation-level rollups from `conversations` + `messages` and call
level detail from `session_call_logs` (written by agent_runner at finalize).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Conversation, Message
from app.db.models.session_log import SessionCallLog
from app.db.models.user import User

# status rank: higher wins when rolling up a conversation's messages
_STATUS_RANK = case(
    (Message.status == "error", 4),
    (Message.status == "streaming", 3),
    (Message.status == "cancelled", 2),
    else_=1,
)
_RANK_TO_STATUS = {4: "fail", 3: "running", 2: "cancelled", 1: "success"}

_TERMINAL_TEXT = 300  # truncate long texts in list rows


def _trunc(text: str | None, n: int = _TERMINAL_TEXT) -> str | None:
    if not text:
        return text
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _parse_date(s: str | None) -> datetime | None:
    """Parse %Y-%m-%d as UTC start-of-day; None on garbage input."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _first_input_expr():
    """Text of the conversation's first user message (JSONB content.text)."""
    return (
        select(Message.content["text"].astext)
        .where(
            Message.conversation_id == Conversation.id,
            Message.role == "user",
        )
        .correlate(Conversation)  # keep Message local (outer query also joins it)
        .order_by(Message.created_at)
        .limit(1)
        .scalar_subquery()
    )


def _last_output_expr():
    """Text of the latest agent/roundtable message (merged for roundtable)."""
    return (
        select(
            func.coalesce(
                Message.content["merged"]["text"].astext,
                Message.content["text"].astext,
                "",
            )
        )
        .where(
            Message.conversation_id == Conversation.id,
            Message.role.in_(("agent", "roundtable")),
        )
        .correlate(Conversation)
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )


def _base_stmt():
    """Conversation rows with aggregated rollup columns, newest first.

    user_name resolves to the conversation owner for personal chats and to
    the TEAM name for group chats (the header summarizes the group, not who
    opened the chat).
    """
    from app.db.models.team import Team
    return (
        select(
            Conversation.id,
            Conversation.title,
            Conversation.type,
            Conversation.acp_session_id,
            Conversation.primary_agent_id,
            Conversation.profile_id,
            Conversation.created_at,
            User.name.label("user_name"),
            User.email.label("user_email"),
            Team.name.label("team_name"),
            _first_input_expr().label("first_input"),
            _last_output_expr().label("last_output"),
            func.count(Message.id).filter(Message.role == "user").label("turn_count"),
            func.min(Message.created_at).filter(Message.role == "user").label("first_user_ts"),
            func.max(_STATUS_RANK).label("status_rank"),
            func.max(Message.updated_at).label("last_activity"),
        )
        .join(User, User.id == Conversation.owner_id)
        .join(Message, Message.conversation_id == Conversation.id)
        .outerjoin(Team, Team.id == Conversation.team_id)
        .group_by(Conversation.id, User.name, User.email, Team.name)
        .order_by(func.max(Message.updated_at).desc())
    )


def _apply_filters(stmt, *, date_from, date_to, source, status, q) -> None:
    if source in ("personal", "group"):
        stmt = stmt.where(Conversation.type == source)
    if status in _RANK_TO_STATUS.values():
        rank = {v: k for k, v in _RANK_TO_STATUS.items()}[status]
        stmt = stmt.having(func.max(_STATUS_RANK) == rank)
    if date_from:
        dt = _parse_date(date_from)
        if dt:
            stmt = stmt.having(func.max(Message.updated_at) >= dt)
    if date_to:
        dt = _parse_date(date_to)
        if dt:
            stmt = stmt.having(func.max(Message.updated_at) < dt + timedelta(days=1))
    if q and q.strip():
        term = q.strip()
        stmt = stmt.where(
            User.name.ilike(f"%{term}%")
            | User.email.ilike(f"%{term}%")
            | _first_input_expr().ilike(f"%{term}%")
        )
    return stmt


async def _call_counts(db: AsyncSession) -> dict[uuid.UUID, tuple[int, int]]:
    """conversation_id -> (model_call_count, tool_call_count)."""
    rows = (
        await db.execute(
            select(
                SessionCallLog.conversation_id,
                func.count(SessionCallLog.id).filter(SessionCallLog.kind == "model"),
                func.count(SessionCallLog.id).filter(SessionCallLog.kind == "tool"),
            ).group_by(SessionCallLog.conversation_id)
        )
    ).all()
    return {r[0]: (int(r[1]), int(r[2])) for r in rows}


def _row_to_item(row, counts: dict[uuid.UUID, tuple[int, int]]) -> dict:
    model_count, tool_count = counts.get(row.id, (0, 0))
    duration_ms = None
    if row.last_activity is not None and row.first_user_ts is not None:
        # Turn span: first user message → last activity (approximation for
        # conversations without call logs; call logs give per-call detail).
        duration_ms = max(
            0, int((row.last_activity - row.first_user_ts).total_seconds() * 1000)
        )
    return {
        "id": str(row.id),
        "title": row.title,
        "source": row.type,  # personal=对话 | group=工作
        # Group rows summarize the team; personal rows the owner.
        "user_name": (row.team_name or row.title) if row.type == "group" else row.user_name,
        "user_email": row.user_email,
        "first_input": _trunc(row.first_input),
        "last_output": _trunc(row.last_output),
        "status": _RANK_TO_STATUS.get(row.status_rank, "success"),
        "turn_count": int(row.turn_count or 0),
        "model_calls": model_count,
        "tool_calls": tool_count,
        "duration_ms": duration_ms,
        "session_id": str(row.id),
        "acp_session_id": row.acp_session_id,
        "last_activity": row.last_activity,
        "created_at": row.created_at,
    }


async def list_sessions(
    db: AsyncSession,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Pageable conversation rollups with total count."""
    stmt = _apply_filters(
        _base_stmt(), date_from=date_from, date_to=date_to,
        source=source, status=status, q=q,
    )
    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    )
    rows = (
        await db.execute(
            stmt.offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    counts = await _call_counts(db)
    items = [_row_to_item(r, counts) for r in rows]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


async def export_rows(
    db: AsyncSession,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 5000,
) -> list[dict]:
    """Same rollups as list_sessions but unbounded (for CSV export)."""
    stmt = _apply_filters(
        _base_stmt(), date_from=date_from, date_to=date_to,
        source=source, status=status, q=q,
    ).limit(min(limit, 5000))
    rows = (await db.execute(stmt)).all()
    counts = await _call_counts(db)
    return [_row_to_item(r, counts) for r in rows]


async def session_detail(db: AsyncSession, conversation_id: uuid.UUID) -> dict | None:
    """Full detail for one conversation: rollup + per-turn log.

    Each turn = one user message + the following agent reply (text, thinking,
    status) + its model/tool calls — rendered as Markdown in the admin UI.
    """
    convo = await db.get(Conversation, conversation_id)
    if convo is None:
        return None
    owner = await db.get(User, convo.owner_id)

    # Group chats: the header summarizes the TEAM/GROUP, not the conversation
    # owner — the person who opened the chat is rarely who typed the messages.
    group_name: str | None = None
    if convo.type == "group":
        if convo.team_id:
            from app.db.models.team import Team
            team = await db.get(Team, convo.team_id)
            if team:
                group_name = team.name or None
        group_name = group_name or convo.title or None

    msgs = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq)
        )
    ).scalars().all()

    # Batch-load sender names for every user message (group chats have many
    # distinct senders — N+1 prevention).
    sender_ids = {
        m.owner_id for m in msgs
        if m.role == "user" and m.owner_id is not None
    }
    sender_names: dict[uuid.UUID, str] = {}
    if sender_ids:
        sender_rows = (
            await db.execute(
                select(User.id, User.name).where(User.id.in_(list(sender_ids)))
            )
        ).all()
        sender_names = {r[0]: r[1] for r in sender_rows}

    def _msg_text(m: Message) -> str | None:
        content = m.content or {}
        merged = content.get("merged") or {}
        return (
            (merged.get("text") if isinstance(merged, dict) else None)
            or content.get("text")
        )

    first_input = None
    last_output = None
    turn_count = 0
    status_rank = 1
    last_activity = convo.created_at
    turns: list[dict] = []
    cur: dict | None = None
    for m in msgs:
        if m.updated_at and m.updated_at > last_activity:
            last_activity = m.updated_at
        if m.status == "error":
            status_rank = max(status_rank, 4)
        elif m.status == "streaming":
            status_rank = max(status_rank, 3)
        elif m.status == "cancelled":
            status_rank = max(status_rank, 2)
        if m.role == "user":
            if cur is not None:
                turns.append(cur)
            cur = {
                "index": len(turns) + 1,
                "user_text": (m.content or {}).get("text"),
                "user_name": sender_names.get(m.owner_id) if m.owner_id else None,
                "agent_text": None,
                "thinking": None,
                "status": "complete",
                "message_id": None,
                "duration_ms": None,
                "user_ts": m.created_at,
                "agent_updated": None,
                "calls": [],
                "replies": None,
            }
            turn_count += 1
            if first_input is None:
                first_input = cur["user_text"]
        elif m.role in ("agent", "roundtable"):
            out = _msg_text(m)
            if out:
                last_output = out
            if cur is None:
                # agent reply without a preceding user turn (e.g. scheduled
                # task) — open its own turn.
                cur = {
                    "index": len(turns) + 1,
                    "user_text": None, "user_name": None, "agent_text": None, "thinking": None,
                    "status": m.status, "message_id": None, "duration_ms": None,
                    "user_ts": None, "agent_updated": None, "calls": [],
                    "replies": None,
                }
            cur["agent_text"] = out
            cur["thinking"] = (m.content or {}).get("thinking")
            cur["status"] = m.status
            cur["message_id"] = str(m.id)
            cur["agent_updated"] = m.updated_at or m.created_at
            # Roundtable: expose each AI's reply (text + thinking) so the
            # admin log shows the full parallel discussion, not just merged.
            content = m.content or {}
            replies_raw = content.get("replies")
            if isinstance(replies_raw, list) and replies_raw:
                merged_meta = content.get("merged") or {}
                cur["replies"] = [
                    {
                        "agent_id": r.get("agent_id", ""),
                        "profile_id": r.get("profile_id"),
                        "text": r.get("text", ""),
                        "status": r.get("status", "complete"),
                        "thinking": r.get("thinking") or None,
                        "calls": [],
                    }
                    for r in replies_raw
                    if isinstance(r, dict)
                ]
                # Merge summary thinking (captured from the merge agent).
                if cur["thinking"] is None:
                    merged_thinking = (
                        merged_meta.get("thinking") if isinstance(merged_meta, dict) else None
                    )
                    cur["thinking"] = merged_thinking or None
    if cur is not None:
        turns.append(cur)

    calls = (
        await db.execute(
            select(SessionCallLog)
            .where(SessionCallLog.conversation_id == conversation_id)
            .order_by(SessionCallLog.started_at, SessionCallLog.id)
        )
    ).scalars().all()

    model_count = sum(1 for c in calls if c.kind == "model")
    tool_count = sum(1 for c in calls if c.kind == "tool")
    started, ended = None, None
    for c in calls:
        if c.started_at and (started is None or c.started_at < started):
            started = c.started_at
        if c.ended_at and (ended is None or c.ended_at > ended):
            ended = c.ended_at
    duration_ms = int((ended - started).total_seconds() * 1000) if started and ended else None

    def _call_to_dict(c: SessionCallLog) -> dict:
        return {
            "id": c.id,
            "kind": c.kind,
            "name": c.name,
            "tool_kind": c.tool_kind,
            "status": c.status,
            "duration_ms": c.duration_ms,
            "tokens_in": c.tokens_in,
            "tokens_out": c.tokens_out,
            "started_at": c.started_at,
            "ended_at": c.ended_at,
        }

    # Attach calls to their turn (keyed by the agent reply message) and derive
    # the per-turn total duration: call span when calls exist, otherwise the
    # user-message → agent-reply timestamps. Roundtable calls are additionally
    # split per AI (agent_id) for the per-reply cards.
    calls_by_msg: dict[str, list[dict]] = {}
    calls_by_msg_agent: dict[tuple[str, str], list[dict]] = {}
    for c in calls:
        c_dict = _call_to_dict(c)
        calls_by_msg.setdefault(str(c.message_id), []).append(c_dict)
        if c.agent_id:
            calls_by_msg_agent.setdefault((str(c.message_id), c.agent_id), []).append(c_dict)
    for t in turns:
        if t["message_id"]:
            t["calls"] = calls_by_msg.get(t["message_id"], [])
        if t["replies"]:
            # Per-AI call overview inside each reply card.
            for r in t["replies"]:
                if t["message_id"]:
                    r["calls"] = calls_by_msg_agent.get(
                        (t["message_id"], r["agent_id"]), [],
                    )
        if t["calls"]:
            t_started = min(c["started_at"] for c in t["calls"] if c["started_at"])
            t_ended = max(c["ended_at"] for c in t["calls"] if c["ended_at"])
            if t_started and t_ended:
                t["duration_ms"] = int((t_ended - t_started).total_seconds() * 1000)
        if t["duration_ms"] is None and t["user_ts"] and t["agent_updated"]:
            t["duration_ms"] = max(0, int((t["agent_updated"] - t["user_ts"]).total_seconds() * 1000))
        t.pop("user_ts", None)
        t.pop("agent_updated", None)

    # Assistant name: the bound Profile's display name (e.g. "emotion-master")
    # falls back to its handle, then the primary agent id.
    from app.db.models.agent import Profile as ProfileModel
    profile_name: str | None = None
    if convo.profile_id:
        try:
            profile = await db.get(ProfileModel, uuid.UUID(str(convo.profile_id)))
            if profile:
                profile_name = profile.name or profile.handle or None
        except (ValueError, TypeError):
            profile_name = None

    return {
        "id": str(convo.id),
        "title": convo.title,
        "source": convo.type,
        # Group chats show the team/group name instead of the chat owner.
        "user_name": group_name or (owner.name if owner else None),
        "user_email": owner.email if owner else None,
        "first_input": first_input,
        "last_output": last_output,
        "status": _RANK_TO_STATUS.get(status_rank, "success"),
        "turn_count": turn_count,
        "model_calls": model_count,
        "tool_calls": tool_count,
        "duration_ms": duration_ms,
        "session_id": str(convo.id),
        "acp_session_id": convo.acp_session_id,
        "agent": convo.primary_agent_id,
        "profile_name": profile_name,
        "profile_id": convo.profile_id,
        "team_id": str(convo.team_id) if convo.team_id else None,
        "created_at": convo.created_at,
        "last_activity": last_activity,
        "turns": turns,
    }
