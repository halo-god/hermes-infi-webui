"""Optimizes one skill's content against its eval dataset (dataset.py).

Two implementations behind one dispatcher (`run_evolution`):
  - `_run_evolution_stub` (Stage D1): deterministic, zero-LLM, exists purely
    to exercise the queue/gate/proposal-review plumbing. Still the active
    path whenever `skill_evolution_enabled` is off or unconfigured — the
    kill switch's "off" state must never spend money, so it degrades to
    this rather than to a hard error.
  - `_run_evolution_dspy` (Stage D2): the real optimizer. Builds a one-Predict
    dspy.Module whose only tunable text is the skill's content, and runs
    dspy.GEPA against it using an LLM-judge metric (metric.py) for textual
    feedback. All direct-LLM-API surface (dspy.LM construction, the actual
    judge calls) is confined to this module — the FastAPI process and the
    ACP-serving runner paths never import dspy or construct a client.

  v2 (out of scope here): replace the cheap LLM-judge metric with one that
  runs a real `hermes` ACP session per candidate (acp_persona.py) for
  higher-fidelity scoring on tool-use-heavy skills, at much higher cost.
"""
from __future__ import annotations

import asyncio
import difflib
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.memory import AgentSkill
from skill_evolution.dataset import DatasetExample, build_dataset


class EvolutionGateFailure(Exception):
    """Raised when a candidate fails a pre-proposal gate (score, size, diff
    ratio). Callers must not create a SkillProposal row when this is raised —
    the run simply produced no candidate worth a human's time."""


@dataclass
class EvolutionResult:
    proposed_content: str
    rationale: str
    eval_score_before: float
    eval_score_after: float
    diff_ratio: float
    dataset_summary: dict = field(default_factory=dict)


async def run_evolution(db: AsyncSession, skill: AgentSkill) -> EvolutionResult:
    """Dispatch to the real optimizer once an admin has both enabled it and
    configured a key; otherwise fall back to the free Stage D1 stub."""
    if settings.skill_evolution_enabled and settings.skill_evolution_llm_api_key:
        return await _run_evolution_dspy(db, skill)
    return await _run_evolution_stub(db, skill)


async def _run_evolution_stub(db: AsyncSession, skill: AgentSkill) -> EvolutionResult:
    """This stub never calls an LLM: `eval_score_before` is a fixed placeholder
    and the candidate is a deterministic textual append, so the numbers here
    are meaningless as quality signals — only useful for exercising the
    pipeline (queue, gates, proposal storage, approval flow) end to end.
    """
    _examples, summary = await build_dataset(db, skill)

    eval_score_before = 0.5
    proposed_content = (
        skill.content.rstrip() + "\n\n（补充说明：回答时请给出更具体的下一步操作建议。）"
    )
    eval_score_after = eval_score_before + 0.1
    diff_ratio = 1 - difflib.SequenceMatcher(None, skill.content, proposed_content).ratio()

    _check_gates(eval_score_before, eval_score_after, proposed_content, diff_ratio)

    return EvolutionResult(
        proposed_content=proposed_content,
        rationale="[stub] 占位优化器：追加固定说明句，用于验证队列/门禁/审核流程，非真实优化结果。",
        eval_score_before=eval_score_before,
        eval_score_after=eval_score_after,
        diff_ratio=diff_ratio,
        dataset_summary=summary.to_dict(),
    )


def _build_task_lm():
    import dspy

    return dspy.LM(
        settings.skill_evolution_llm_model,
        api_key=settings.skill_evolution_llm_api_key,
        api_base=settings.skill_evolution_llm_api_base or None,
    )


def _to_dspy_examples(examples: list[DatasetExample]) -> tuple[list, list]:
    """Splits into train/val, held-out-last-slice style (dataset.py already
    orders real examples newest-first; synthetic ones have no ordering, so a
    fixed slice is as good as any and keeps the split deterministic)."""
    import dspy

    dspy_examples = [
        dspy.Example(
            trigger_query=e.query,
            skill_content_snapshot=e.skill_content_snapshot,
            reference_reply=e.output_trace or "",
            label=e.label or "",
        ).with_inputs("trigger_query")
        for e in examples
    ]
    val_size = max(1, len(dspy_examples) // 5)
    return dspy_examples[val_size:], dspy_examples[:val_size]


def _average_score(module, examples: list, metric) -> float:
    if not examples:
        return 0.0
    total = 0.0
    for ex in examples:
        pred = module(**ex.inputs())
        total += metric(ex, pred).score
    return total / len(examples)


async def _run_evolution_dspy(db: AsyncSession, skill: AgentSkill) -> EvolutionResult:
    import dspy

    from skill_evolution.metric import CallBudget, SkillJudgeSignature, build_gepa_metric
    from skill_evolution.signatures import SkillModule

    examples, summary = await build_dataset(db, skill)
    if len(examples) < 2:
        raise EvolutionGateFailure("数据集样本不足，至少需要 2 条才能划分训练/验证集")

    trainset, valset = _to_dspy_examples(examples)
    task_lm = _build_task_lm()
    judge = dspy.Predict(SkillJudgeSignature)

    # The hard backstop (on top of GEPA's own reflective-call budget): shared
    # across the before/after eval passes AND every call GEPA makes internally,
    # so the configured cap is never exceeded no matter how GEPA spends it.
    budget = CallBudget(remaining=settings.skill_evolution_llm_max_calls_per_run)
    metric = build_gepa_metric(judge, task_lm, budget)

    def _sync_optimize() -> tuple[str, float, float]:
        with dspy.context(lm=task_lm):
            module = SkillModule(skill.content)
            score_before = _average_score(module, valset, metric)

            # Reserve this pass's calls out of the shared budget so GEPA
            # can't spend it all before we get to measure the result.
            gepa_budget = max(1, budget.remaining - len(valset))
            optimizer = dspy.GEPA(
                metric=metric,
                max_metric_calls=gepa_budget,
                reflection_lm=task_lm,
                track_stats=True,
            )
            optimized = optimizer.compile(module, trainset=trainset, valset=valset)

            score_after = _average_score(optimized, valset, metric)
            proposed_content = optimized.respond.signature.instructions
        return proposed_content, score_before, score_after

    proposed_content, score_before, score_after = await asyncio.to_thread(_sync_optimize)

    diff_ratio = 1 - difflib.SequenceMatcher(None, skill.content, proposed_content).ratio()
    _check_gates(score_before, score_after, proposed_content, diff_ratio)

    return EvolutionResult(
        proposed_content=proposed_content,
        rationale=(
            "DSPy GEPA 优化：基于真实命中/合成样本，由裁判 LLM 反思生成的新技能内容"
            f"（真实样本 {summary.real_count} 条，合成样本 {summary.synthetic_count} 条）。"
        ),
        eval_score_before=score_before,
        eval_score_after=score_after,
        diff_ratio=diff_ratio,
        dataset_summary=summary.to_dict(),
    )


def _check_gates(
    score_before: float, score_after: float, proposed_content: str, diff_ratio: float,
) -> None:
    if score_after - score_before < settings.skill_evolution_min_score_improvement:
        raise EvolutionGateFailure(
            f"候选分数提升不足：{score_after - score_before:.3f} < "
            f"{settings.skill_evolution_min_score_improvement}"
        )
    content_bytes = len(proposed_content.encode("utf-8"))
    if content_bytes > settings.skill_evolution_max_content_bytes:
        raise EvolutionGateFailure(
            f"候选内容超出大小上限：{content_bytes} > {settings.skill_evolution_max_content_bytes} 字节"
        )
    if diff_ratio > settings.skill_evolution_max_content_diff_ratio:
        raise EvolutionGateFailure(
            f"改动幅度超出上限：{diff_ratio:.3f} > {settings.skill_evolution_max_content_diff_ratio}"
        )
