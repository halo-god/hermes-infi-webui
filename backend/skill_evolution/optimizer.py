"""Stage D1 stub optimizer — validates the queue/gate/proposal-review
plumbing without any LLM dependency. `run_evolution()`'s scoring and
rewrite here are placeholders; Stage D2 replaces both with a real DSPy
program optimized by GEPA against the dataset this module already builds
via dataset.py, while keeping the same gates and EvolutionResult shape so
runner_skill_evolution.py and the API layer don't need to change again.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.memory import AgentSkill
from skill_evolution.dataset import DatasetSummary, build_dataset


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
    """Build the eval dataset, produce a candidate rewrite, and check it
    against the same gates the real DSPy/GEPA optimizer will use.

    This stub never calls an LLM: `eval_score_before` is a fixed placeholder
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
        dataset_summary=_summary_dict(summary),
    )


def _summary_dict(summary: DatasetSummary) -> dict:
    return summary.to_dict()


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
