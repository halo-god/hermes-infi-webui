"""P2-4: optimizes one Profile's system_prompt against its eval dataset.

Reuses the same GEPA + LLM-judge infrastructure as optimizer.py (skill
evolution), but swaps SkillModule → ProfileModule and reads from
profile_firings instead of skill_firings. The kill-switch and config reuse the
skill_evolution_* settings (single auxiliary-LLM channel) — there's no separate
profile_evolution_enabled flag; an admin enabling skill evolution implicitly
enables profile evolution too.

Same gate discipline: a candidate that doesn't beat the baseline by
min_score_improvement, or that strays too far (diff_ratio), is rejected without
creating a proposal.
"""
from __future__ import annotations

import asyncio
import difflib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.agent import Profile
from app.db.models.conversation import Message
from app.db.models.profile_evolution import ProfileFiring
from skill_evolution.dataset import DatasetExample, DatasetSummary
from skill_evolution.optimizer import EvolutionGateFailure, _build_task_lm


@dataclass
class ProfileEvolutionResult:
    proposed_prompt: str
    rationale: str
    eval_score_before: float
    eval_score_after: float
    diff_ratio: float
    dataset_summary: dict


async def run_profile_evolution(db: AsyncSession, profile: Profile) -> ProfileEvolutionResult:
    """Dispatch like run_evolution: real dspy path when enabled+keyed, else stub."""
    if settings.skill_evolution_enabled and settings.skill_evolution_llm_api_key:
        return await _run_profile_evolution_dspy(db, profile)
    return _run_profile_evolution_stub(profile)


def _run_profile_evolution_stub(profile: Profile) -> ProfileEvolutionResult:
    """Zero-LLM stub: mirrors the skill stub. Meaningless scores, deterministic
    textual append — only for exercising the plumbing when evolution is off."""
    current = profile.system_prompt or ""
    proposed = current + "\n\n（优化提示：回答前先确认用户意图，回答后主动询问是否需要补充。）"
    return ProfileEvolutionResult(
        proposed_prompt=proposed,
        rationale="占位优化（未启用 LLM，仅走通管线）",
        eval_score_before=0.5,
        eval_score_after=0.6,
        diff_ratio=1 - difflib.SequenceMatcher(None, current, proposed).ratio(),
        dataset_summary={"real_count": 0, "synthetic_count": 0, "note": "stub"},
    )


async def _build_profile_dataset(
    db: AsyncSession, profile: Profile,
) -> tuple[list[DatasetExample], DatasetSummary]:
    """Build an eval dataset from this profile's firings (newest-first, capped)."""
    max_firings = settings.skill_evolution_max_firings_per_skill
    excerpt_chars = settings.skill_evolution_firing_excerpt_chars
    total_chars = settings.skill_evolution_dataset_input_chars

    rows = (await db.execute(
        select(ProfileFiring, Message)
        .join(Message, ProfileFiring.message_id == Message.id)
        .where(ProfileFiring.profile_id == profile.id)
        .order_by(ProfileFiring.created_at.desc())
        .limit(max_firings)
    )).all()

    examples: list[DatasetExample] = []
    used = 0
    for firing, msg in rows:
        if used >= total_chars:
            break
        query = (firing.trigger_query_excerpt or "")[:excerpt_chars]
        if not query:
            continue
        # Reactions on the agent message label the example.
        reactions = msg.reactions or {} if isinstance(msg.reactions, dict) else {}
        label = None
        if reactions.get("👍"):
            label = "positive"
        elif reactions.get("👎"):
            label = "negative"
        agent_text = ""
        content = msg.content or {}
        if isinstance(content, dict):
            agent_text = (content.get("text") or "")[:excerpt_chars]
        examples.append(DatasetExample(
            query=query,
            skill_content_snapshot=profile.system_prompt or "",
            output_trace=agent_text,
            label=label,
            source="real",
        ))
        used += len(query) + len(agent_text)

    summary = DatasetSummary(
        real_count=len(examples), synthetic_count=0,
        earliest=rows[-1][0].created_at if rows else None,
        latest=rows[0][0].created_at if rows else None,
    )
    return examples, summary


async def _run_profile_evolution_dspy(
    db: AsyncSession, profile: Profile,
) -> ProfileEvolutionResult:
    import dspy

    from skill_evolution.metric import CallBudget, SkillJudgeSignature, build_gepa_metric
    from skill_evolution.optimizer import _average_score, _to_dspy_examples
    from skill_evolution.signatures import ProfileModule

    examples, summary = await _build_profile_dataset(db, profile)
    if len(examples) < 2:
        raise EvolutionGateFailure("数据集样本不足，至少需要 2 条才能划分训练/验证集")

    trainset, valset = _to_dspy_examples(examples)
    task_lm = _build_task_lm()
    judge = dspy.Predict(SkillJudgeSignature)
    budget = CallBudget(remaining=settings.skill_evolution_llm_max_calls_per_run)
    metric = build_gepa_metric(judge, task_lm, budget)

    current_prompt = profile.system_prompt or ""

    def _sync_optimize() -> tuple[str, float, float]:
        with dspy.context(lm=task_lm):
            module = ProfileModule(current_prompt)
            score_before = _average_score(module, valset, metric)
            gepa_budget = max(1, budget.remaining - len(valset))
            optimizer = dspy.GEPA(
                metric=metric, max_metric_calls=gepa_budget,
                reflection_lm=task_lm, track_stats=True,
            )
            optimized = optimizer.compile(module, trainset=trainset, valset=valset)
            score_after = _average_score(optimized, valset, metric)
            proposed = optimized.respond.signature.instructions
        return proposed, score_before, score_after

    proposed, score_before, score_after = await asyncio.to_thread(_sync_optimize)
    diff_ratio = 1 - difflib.SequenceMatcher(None, current_prompt, proposed).ratio()

    # Reuse the skill gates (same thresholds apply to prompt rewrites).
    from skill_evolution.optimizer import _check_gates
    _check_gates(score_before, score_after, proposed, diff_ratio)

    return ProfileEvolutionResult(
        proposed_prompt=proposed,
        rationale=(
            "DSPy GEPA 优化：基于该助手的真实命中样本，由裁判 LLM 反思生成的新人设提示词"
            f"（真实样本 {summary.real_count} 条）。"
        ),
        eval_score_before=score_before,
        eval_score_after=score_after,
        diff_ratio=diff_ratio,
        dataset_summary=summary.to_dict(),
    )
