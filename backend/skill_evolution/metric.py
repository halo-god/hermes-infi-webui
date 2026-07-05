"""LLM-judge metric for GEPA: scores a candidate skill's generated reply and
returns textual feedback GEPA's reflective mutation step can act on directly
(GEPA is designed around text feedback, not bare scalars). Deliberately the
cheap version — a judge call, not a real ACP session — see optimizer.py's
module docstring for why a real-session judge is an explicitly deferred v2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import dspy


class SkillJudgeSignature(dspy.Signature):
    """作为质量裁判，评估某条 AI 回复是否满足技能内容规定的要求，并给出 0~1 的分数与简短点评。"""

    trigger_query: str = dspy.InputField(desc="用户消息")
    skill_content: str = dspy.InputField(desc="生成该回复时使用的技能内容（候选提示词）")
    candidate_response: str = dspy.InputField(desc="模型依据技能内容生成的回复")
    reference_reply: str = dspy.InputField(desc="该技能历史上产出的真实回复摘录，可能为空")
    human_label: str = dspy.InputField(desc="人工反馈标签：positive / negative / 空")
    score: float = dspy.OutputField(desc="0 到 1 之间的分数，越高越好")
    feedback: str = dspy.OutputField(desc="一到两句话的点评，说明扣分原因或做得好的地方")


def _safe_score(raw: object) -> float:
    """LLM judge output is untrusted external input — a malformed or
    out-of-range value must degrade to a safe score, not raise into GEPA."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


@dataclass
class CallBudget:
    """Mutable call counter shared across one optimizer run so the judge can
    enforce skill_evolution_llm_max_calls_per_run even though GEPA itself has
    no built-in awareness of our budget."""
    remaining: int

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def build_gepa_metric(judge: dspy.Predict, judge_lm: dspy.LM, budget: CallBudget) -> Callable:
    """Returns a GEPA-compatible metric: (gold, pred, trace, pred_name, pred_trace) -> ScoreWithFeedback."""
    from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> ScoreWithFeedback:
        if not budget.take():
            return ScoreWithFeedback(score=0.0, feedback="已达到本次运行的裁判调用上限，未评分")
        try:
            result = judge(
                trigger_query=gold.trigger_query,
                skill_content=gold.skill_content_snapshot,
                candidate_response=getattr(pred, "response", "") or "",
                reference_reply=getattr(gold, "reference_reply", "") or "",
                human_label=getattr(gold, "label", "") or "",
                lm=judge_lm,
            )
        except Exception as exc:  # noqa: BLE001 — judge output is untrusted; a
            # parse failure or transient API error must degrade this one
            # example's score, not crash the whole GEPA run.
            return ScoreWithFeedback(score=0.0, feedback=f"裁判调用失败（{type(exc).__name__}），本条记为 0 分")
        return ScoreWithFeedback(score=_safe_score(result.score), feedback=result.feedback or "")

    return metric
