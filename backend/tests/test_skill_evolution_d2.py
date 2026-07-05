"""Stage D2: real DSPy + GEPA integration.

These tests exercise our own orchestration code (signatures.py, metric.py,
optimizer.py's dataset<->dspy.Example conversion, budget enforcement, gate
checks) using dspy's real Predict/Module/ChatAdapter machinery with a fully
offline `dspy.utils.DummyLM` — never a real network call. `dspy.GEPA` itself
is monkeypatched out in the end-to-end test: GEPA's internal reflective
prompting is the optimization *framework's* concern (covered by dspy's own
test suite), not something this module's tests should re-verify. What we
verify here is that we call GEPA correctly and handle its output correctly.
"""
from __future__ import annotations

import uuid

import dspy
import pytest
from dspy.utils.dummies import DummyLM

from app.db.models.conversation import Message
from app.db.models.skill_evolution import SkillFiring
from app.services import memory_service
from skill_evolution import optimizer as opt
from skill_evolution.metric import CallBudget, SkillJudgeSignature, build_gepa_metric
from skill_evolution.signatures import SkillModule


async def _mk_user(db, email: str):
    from app.core.security import hash_password
    from app.db.models.user import User
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_skill(db, owner, **kwargs):
    defaults = dict(
        name="限流技能", description="限流相关问题",
        content="回答限流问题时提醒检查 rl:msg:{user} 键",
        trigger_conditions={"keywords": ["限流"]}, owner_id=owner.id,
    )
    defaults.update(kwargs)
    return await memory_service.create_skill(db, **defaults)


async def _mk_conversation(db, owner):
    from app.db.models.conversation import Conversation
    convo = Conversation(owner_id=owner.id, title="会话", primary_agent_id="hermes")
    db.add(convo)
    await db.flush()
    return convo


async def _mk_firing(db, skill, convo, *, reply_text: str, query: str = "限流问题"):
    msg = Message(
        conversation_id=convo.id, role="agent", agent_id="hermes",
        content={"text": reply_text}, status="complete",
    )
    db.add(msg)
    await db.flush()
    firing = SkillFiring(
        skill_id=skill.id, message_id=msg.id, conversation_id=convo.id,
        owner_id=convo.owner_id, trigger_query_excerpt=query,
    )
    db.add(firing)
    await db.flush()


# ── signatures.py: the optimizable dspy program ─────────────────────────────

async def test_skill_module_forward_uses_skill_content_as_instructions():
    lm = DummyLM([{"response": "已经处理好了"}])
    with dspy.context(lm=lm):
        module = SkillModule("回答限流问题时提醒检查 rl:msg:{user} 键")
        assert module.respond.signature.instructions == "回答限流问题时提醒检查 rl:msg:{user} 键"
        pred = module(trigger_query="限流怎么做")
        assert pred.response == "已经处理好了"


# ── metric.py: judge scoring + call budget ──────────────────────────────────

def test_safe_score_clamps_and_handles_malformed_input():
    from skill_evolution.metric import _safe_score
    assert _safe_score(1.5) == 1.0
    assert _safe_score(-0.3) == 0.0
    assert _safe_score(0.42) == pytest.approx(0.42)
    assert _safe_score("not-a-number") == 0.0
    assert _safe_score(None) == 0.0


async def test_judge_metric_scores_via_real_dspy_predict_and_clamps():
    # 1.5 parses as a valid float (dspy's own typed-field parsing accepts it),
    # so this exercises _safe_score's range clamp specifically, through the
    # real Predict/ChatAdapter round trip rather than as a unit test.
    judge_lm = DummyLM([{"score": 1.5, "feedback": "超出范围应被截断到 1"}])
    judge = dspy.Predict(SkillJudgeSignature)
    budget = CallBudget(remaining=10)
    metric = build_gepa_metric(judge, judge_lm, budget)
    gold = dspy.Example(
        trigger_query="限流怎么做", skill_content_snapshot="内容",
        reference_reply="参考回复", label="positive",
    ).with_inputs("trigger_query")
    pred = dspy.Prediction(response="候选回复")

    result = metric(gold, pred)
    assert result.score == 1.0
    assert budget.remaining == 9


async def test_judge_metric_returns_zero_score_when_judge_call_raises():
    """A malformed judge response can raise deep inside dspy's adapter
    parsing (AdapterParseError) rather than just returning a bad value —
    that must degrade this one example's score, not crash the whole run."""
    def _raising_judge(**kwargs):
        raise RuntimeError("judge exploded")

    budget = CallBudget(remaining=10)
    metric = build_gepa_metric(_raising_judge, judge_lm=None, budget=budget)
    gold = dspy.Example(trigger_query="q", skill_content_snapshot="c").with_inputs("trigger_query")
    pred = dspy.Prediction(response="r")

    result = metric(gold, pred)
    assert result.score == 0.0
    assert "裁判调用失败" in result.feedback
    assert budget.remaining == 9


async def test_judge_metric_stops_calling_judge_once_budget_exhausted():
    judge_lm = DummyLM([{"score": 0.9, "feedback": "不该被用到"}])
    judge = dspy.Predict(SkillJudgeSignature)
    budget = CallBudget(remaining=0)
    metric = build_gepa_metric(judge, judge_lm, budget)
    gold = dspy.Example(trigger_query="q", skill_content_snapshot="c").with_inputs("trigger_query")
    pred = dspy.Prediction(response="r")

    result = metric(gold, pred)
    assert result.score == 0.0
    assert "上限" in result.feedback


# ── optimizer.py: dataset<->dspy.Example conversion + averaging ─────────────

def test_to_dspy_examples_splits_train_val_and_marks_only_trigger_query_as_input():
    from skill_evolution.dataset import DatasetExample
    examples = [
        DatasetExample(query=f"q{i}", skill_content_snapshot="c", output_trace=f"r{i}", label=None, source="real")
        for i in range(5)
    ]
    trainset, valset = opt._to_dspy_examples(examples)
    assert len(valset) == 1
    assert len(trainset) == 4
    assert list(valset[0].inputs().keys()) == ["trigger_query"]


async def test_average_score_computes_mean_over_examples():
    lm = DummyLM([{"response": "r1"}, {"response": "r2"}])
    judge_lm = DummyLM([{"score": 0.2, "feedback": "差"}, {"score": 0.8, "feedback": "好"}])
    judge = dspy.Predict(SkillJudgeSignature)
    budget = CallBudget(remaining=10)
    metric = build_gepa_metric(judge, judge_lm, budget)

    with dspy.context(lm=lm):
        module = SkillModule("技能内容")
        examples = [
            dspy.Example(trigger_query=f"q{i}", skill_content_snapshot="技能内容").with_inputs("trigger_query")
            for i in range(2)
        ]
        avg = opt._average_score(module, examples, metric)
    assert avg == pytest.approx(0.5)


# ── optimizer.py: end-to-end _run_evolution_dspy with GEPA faked out ────────

class _FakeGEPA:
    """Stands in for dspy.GEPA: our tests should verify optimizer.py calls
    GEPA correctly and handles its output correctly, not re-verify GEPA's
    own reflective-prompting internals (that's dspy's test suite's job)."""

    def __init__(self, metric, max_metric_calls=None, reflection_lm=None, track_stats=False, **kw):
        self.metric = metric

    def compile(self, student, *, trainset, valset=None):
        new_content = student.respond.signature.instructions + "\n更新：请补充下一步操作建议。"
        return SkillModule(new_content)


async def test_run_evolution_dspy_end_to_end(db, monkeypatch):
    from app.config import settings

    owner = await _mk_user(db, "d2-owner@h.io")
    skill = await _mk_skill(db, owner)
    convo = await _mk_conversation(db, owner)
    for i in range(5):
        await _mk_firing(db, skill, convo, reply_text=f"历史回复{i}")

    monkeypatch.setattr(settings, "skill_evolution_enabled", True)
    monkeypatch.setattr(settings, "skill_evolution_llm_api_key", "fake-key-not-a-real-secret")
    monkeypatch.setattr(settings, "skill_evolution_llm_model", "dummy/dummy")

    # 1 example in valset (5 real examples -> val_size = max(1, 5//5) = 1), so
    # each of the before/after eval passes makes exactly one [task, judge] pair.
    fake_lm = DummyLM([
        {"response": "优化前回复"}, {"score": 0.3, "feedback": "不够具体"},
        {"response": "优化后回复"}, {"score": 0.9, "feedback": "很好"},
    ])
    monkeypatch.setattr(opt, "_build_task_lm", lambda: fake_lm)
    monkeypatch.setattr(dspy, "GEPA", _FakeGEPA)

    result = await opt.run_evolution(db, skill)

    assert result.eval_score_before == pytest.approx(0.3)
    assert result.eval_score_after == pytest.approx(0.9)
    assert result.proposed_content == skill.content + "\n更新：请补充下一步操作建议。"
    assert result.dataset_summary["real_count"] == 5


async def test_run_evolution_dspy_end_to_end_rejects_on_score_gate(db, monkeypatch):
    from app.config import settings

    owner = await _mk_user(db, "d2-owner2@h.io")
    skill = await _mk_skill(db, owner, name="另一个技能")
    convo = await _mk_conversation(db, owner)
    for i in range(5):
        await _mk_firing(db, skill, convo, reply_text=f"历史回复{i}")

    monkeypatch.setattr(settings, "skill_evolution_enabled", True)
    monkeypatch.setattr(settings, "skill_evolution_llm_api_key", "fake-key-not-a-real-secret")
    monkeypatch.setattr(settings, "skill_evolution_llm_model", "dummy/dummy")

    # Same score before/after -> fails the min-improvement gate.
    fake_lm = DummyLM([
        {"response": "回复A"}, {"score": 0.5, "feedback": "一般"},
        {"response": "回复B"}, {"score": 0.5, "feedback": "一般"},
    ])
    monkeypatch.setattr(opt, "_build_task_lm", lambda: fake_lm)
    monkeypatch.setattr(dspy, "GEPA", _FakeGEPA)

    with pytest.raises(opt.EvolutionGateFailure, match="分数提升不足"):
        await opt.run_evolution(db, skill)


async def test_run_evolution_falls_back_to_stub_when_enabled_but_no_key(db, monkeypatch):
    from app.config import settings

    owner = await _mk_user(db, "d2-owner3@h.io")
    skill = await _mk_skill(db, owner)

    monkeypatch.setattr(settings, "skill_evolution_enabled", True)
    monkeypatch.setattr(settings, "skill_evolution_llm_api_key", "")

    result = await opt.run_evolution(db, skill)

    assert result.rationale.startswith("[stub]")
