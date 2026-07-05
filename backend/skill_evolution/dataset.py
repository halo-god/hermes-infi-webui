"""Eval-dataset builder for the self-evolving skills pipeline.

Two sources, mirroring hermes-agent's own "synthetic or real session
history" approach: real SkillFiring rows joined to the agent Message they
produced (labeled by any thumbs-up/down reaction — see ChatView.vue's
msg-tools row and the group emoji picker, both backed by the same
toggle_reaction() endpoint), topped up with synthetic examples when a skill
has too few real firings to be a trustworthy eval set.

Produces a framework-agnostic list of DatasetExample — conversion to
dspy.Example happens only in optimizer.py (a later stage), so this module
has zero dependency on dspy-ai and can be built/tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.conversation import Message
from app.db.models.memory import AgentSkill
from app.db.models.skill_evolution import SkillFiring


@dataclass
class DatasetExample:
    query: str
    skill_content_snapshot: str
    output_trace: str | None
    label: str | None  # "positive" | "negative" | None
    source: str  # "real" | "synthetic"


@dataclass
class DatasetSummary:
    real_count: int
    synthetic_count: int
    earliest: datetime | None
    latest: datetime | None

    def to_dict(self) -> dict:
        return {
            "real_count": self.real_count,
            "synthetic_count": self.synthetic_count,
            "earliest": self.earliest.isoformat() if self.earliest else None,
            "latest": self.latest.isoformat() if self.latest else None,
        }


# Injected by optimizer.py once a later stage wires in a real LLM client.
# None here just means "skip synthetic generation" — a skill with too few
# real firings simply gets a smaller (real-only) dataset, not an error.
SyntheticGenerator = Callable[[AgentSkill, int], Awaitable[list[DatasetExample]]]


def _label_from_reactions(reactions: dict) -> str | None:
    if reactions.get("👍"):
        return "positive"
    if reactions.get("👎"):
        return "negative"
    return None


async def build_real_examples(
    db: AsyncSession, skill: AgentSkill, *,
    limit: int | None = None, max_total_chars: int | None = None,
) -> tuple[list[DatasetExample], DatasetSummary]:
    """Pull the skill's most recent firings, newest first, joined to the
    agent reply they produced. Bounded the same way handle_memory_consolidate
    bounds its transcript excerpt: a hard row cap plus a running character
    budget, so one pathologically long reply can't dominate the dataset.

    NOTE: skill_content_snapshot uses the skill's CURRENT content for every
    real example — SkillFiring doesn't version content at firing time. If a
    skill has been hand-edited since some of its firings, those examples
    describe old behavior under new text. Acceptable for v1; flagged here
    rather than silently assumed.
    """
    limit = limit if limit is not None else settings.skill_evolution_max_firings_per_skill
    max_total_chars = (
        max_total_chars if max_total_chars is not None else settings.skill_evolution_dataset_input_chars
    )
    excerpt_chars = settings.skill_evolution_firing_excerpt_chars

    stmt = (
        select(SkillFiring, Message)
        .join(Message, Message.id == SkillFiring.message_id)
        .where(SkillFiring.skill_id == skill.id)
        .order_by(SkillFiring.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    examples: list[DatasetExample] = []
    used = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    for firing, message in rows:
        reply_text = (message.content or {}).get("text", "") or ""
        if not reply_text:
            continue
        if used + len(reply_text) > max_total_chars:
            reply_text = reply_text[: max(0, max_total_chars - used)]
        if not reply_text:
            break
        used += len(reply_text)
        examples.append(DatasetExample(
            query=firing.trigger_query_excerpt[:excerpt_chars],
            skill_content_snapshot=skill.content,
            output_trace=reply_text,
            label=_label_from_reactions(message.reactions or {}),
            source="real",
        ))
        if earliest is None or firing.created_at < earliest:
            earliest = firing.created_at
        if latest is None or firing.created_at > latest:
            latest = firing.created_at
        if used >= max_total_chars:
            break

    summary = DatasetSummary(real_count=len(examples), synthetic_count=0, earliest=earliest, latest=latest)
    return examples, summary


async def build_dataset(
    db: AsyncSession, skill: AgentSkill, *,
    min_real: int | None = None,
    synthetic_count: int | None = None,
    synthetic_generator: SyntheticGenerator | None = None,
) -> tuple[list[DatasetExample], DatasetSummary]:
    """Real examples, topped up with synthetic ones when real firings are
    too sparse. `synthetic_generator` is None until the optimizer stage wires
    in a real LLM client — real-only datasets still work (just smaller), so
    this degrades gracefully instead of failing.
    """
    min_real = min_real if min_real is not None else settings.skill_evolution_min_real_firings
    synthetic_count = (
        synthetic_count if synthetic_count is not None else settings.skill_evolution_synthetic_examples
    )

    examples, summary = await build_real_examples(db, skill)
    if len(examples) < min_real and synthetic_generator is not None:
        synthetic = await synthetic_generator(skill, synthetic_count)
        examples = examples + synthetic
        summary = DatasetSummary(
            real_count=summary.real_count, synthetic_count=len(synthetic),
            earliest=summary.earliest, latest=summary.latest,
        )
    return examples, summary
