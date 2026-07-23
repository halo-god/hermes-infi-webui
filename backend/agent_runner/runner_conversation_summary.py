"""P1-2 runner worker: periodic LLM summarisation of conversation history.

Consumed by runner.py when a `type=conversation_summary` task lands on the
Redis Stream. The worker:
  1. Loads the conversation's messages since the last covered_up_to_msg_id.
  2. Filters to genuine user<->agent dialogue (mirrors runner_memory's rules).
  3. Applies per-message + total char budgets.
  4. Calls the summariser (auxiliary LLM via dspy).
  5. Upserts conversation_summaries with the merged result.

Never raises into the runner — failures log and set a Redis error status so
the next turn retries. Idempotent: if there's nothing new to summarise, no-op.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import async_session_maker
from app.db.models.conversation import Conversation, ConversationSummary, Message
from app.services import summarizer

logger = logging.getLogger(__name__)


def _excerpt(msg: Message) -> tuple[str, str] | None:
    """One (role_label, text) pair for the transcript; None to skip.

    Same filtering policy as runner_memory._message_excerpt: keep genuine
    user<->agent dialogue, drop system/error/roundtable/bare-tool-call turns.
    """
    content = msg.content or {}
    if msg.role in ("system",) or msg.status == "error" or msg.role == "roundtable":
        return None
    text = (content.get("text") or "").strip()
    if not text:
        return None
    if msg.role == "agent":
        if content.get("tool_calls") and len(text) < 20:
            return None
        label = "助手"
    elif msg.role == "user":
        label = "用户"
    else:
        return None
    limit = settings.summary_msg_chars
    if len(text) > limit:
        text = text[:limit] + "…"
    return label, text


async def _load_transcript(
    db: AsyncSession, conversation_id: uuid.UUID, after_msg_id: uuid.UUID | None, preserve_recent: int,
) -> tuple[list[tuple[str, str]], uuid.UUID | None, int]:
    """Return (transcript, last_covered_msg_id, covered_count).

    Loads messages older than the most-recent `preserve_recent` (those stay
    verbatim in the prompt). When after_msg_id is set, only loads messages
    AFTER it (incremental summarisation).
    """
    res = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    all_msgs = list(res.scalars().all())
    if not all_msgs:
        return [], None, 0

    # Keep all but the most-recent N for summarisation; the recent ones stay
    # verbatim in the live prompt and don't belong in the summary.
    to_summarise = all_msgs[:-preserve_recent] if len(all_msgs) > preserve_recent else []

    # If resuming, drop everything up to and including after_msg_id.
    if after_msg_id is not None:
        cut = 0
        for i, m in enumerate(to_summarise):
            if m.id == after_msg_id:
                cut = i + 1
                break
        to_summarise = to_summarise[cut:]

    transcript: list[tuple[str, str]] = []
    total_chars = 0
    budget = settings.summary_input_chars
    last_id: uuid.UUID | None = after_msg_id
    for msg in to_summarise:
        pair = _excerpt(msg)
        if pair is None:
            continue
        line = f"{pair[0]}：{pair[1]}"
        if total_chars + len(line) > budget:
            break  # hit the input budget — stop here, resume next run
        transcript.append(pair)
        total_chars += len(line)
        last_id = msg.id

    return transcript, last_id, len(transcript)


async def handle_conversation_summary(task: dict) -> None:
    """Entry point invoked by runner.py's task router."""
    conversation_id_str = task.get("conversation_id")
    if not conversation_id_str:
        logger.warning("conversation_summary task missing conversation_id")
        return
    if not settings.summary_enabled:
        return
    conversation_id = uuid.UUID(conversation_id_str)

    async with async_session_maker() as db:
        convo = await db.get(Conversation, conversation_id)
        if convo is None:
            return

        existing = (await db.execute(
            select(ConversationSummary).where(ConversationSummary.conversation_id == conversation_id)
        )).scalar_one_or_none()

        after_id = existing.covered_up_to_msg_id if existing else None
        transcript, last_id, count = await _load_transcript(
            db, conversation_id, after_id, settings.summary_preserve_recent,
        )

        if count < settings.summary_increment_threshold:
            # Not enough new dialogue since the last run — skip to save cost.
            return

        messages_text = summarizer.build_messages_text(transcript)
        result = await asyncio.to_thread(summarizer.summarize_sync, messages_text)
        if result is None:
            return

        # Merge with the prior summary (incremental: append new decisions/facts).
        if existing and existing.summary:
            merged = (
                f"{existing.summary}\n\n【近期补充】\n{result.summary}"
            )
        else:
            merged = result.summary

        covered_count = (existing.covered_count if existing else 0) + count
        tokens = result.token_estimate + (existing.token_estimate if existing else 0)

        if existing:
            existing.summary = merged
            existing.covered_up_to_msg_id = last_id
            existing.covered_count = covered_count
            existing.token_estimate = tokens
        else:
            db.add(ConversationSummary(
                conversation_id=conversation_id,
                summary=merged,
                covered_up_to_msg_id=last_id,
                covered_count=covered_count,
                token_estimate=tokens,
            ))
        await db.commit()
        logger.info(
            "Summarised conv %s: +%s msgs, %s total covered, ~%s tokens",
            conversation_id_str[:8], count, covered_count, tokens,
        )
