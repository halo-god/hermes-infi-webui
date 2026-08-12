"""Self-evolving skills: runs an evolution pass for one skill and files the
result as a SkillProposal (Stage D1 uses skill_evolution.optimizer's LLM-free
stub; Stage D2 swaps that stub for real DSPy+GEPA without touching this
file). Never writes to AgentSkill directly — approval (Stage E) is the only
path that does."""
from __future__ import annotations
import asyncio

import json
import logging
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.skill_evolution import SkillProposal

try:
    from skill_evolution.optimizer import EvolutionGateFailure, run_evolution
except ImportError:  # dspy (the `skill` extra) not installed — slim Docker image
    logging.getLogger("hermes.runner").warning(
        "skill_evolution unavailable: dspy not installed (pip install '.[skill]')"
    )
    EvolutionGateFailure = None
    run_evolution = None  # type: ignore[assignment]

logger = logging.getLogger("hermes.runner")


async def handle_skill_evolution(task: dict, agents: dict) -> None:
    """Handle a `skill_evolution` task: build the skill's eval dataset, run
    the optimizer, and store a pending SkillProposal if it clears the gates."""
    from app.services import memory_service

    skill_id = task["skill_id"]
    r = R.get_redis()
    status_key = R.skill_evolution_status_key(skill_id)

    if run_evolution is None:
        # dspy missing (e.g. slim Docker image) — mark failed immediately
        # instead of burning the runner's retry budget on ImportError.
        await r.set(
            status_key,
            json.dumps(
                {"status": "error", "detail": "技能进化未启用：缺少 dspy 依赖"},
                ensure_ascii=False,
            ),
            ex=settings.skill_evolution_status_ttl,
        )
        return

    async def _set_status(status: str, detail: str | None = None) -> None:
        payload: dict = {
            "status": status,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        if detail:
            payload["detail"] = detail
        ttl = settings.skill_evolution_lock_ttl if status == "running" else settings.skill_evolution_status_ttl
        await r.set(status_key, json.dumps(payload, ensure_ascii=False), ex=ttl)

    try:
        async with async_session_maker() as db:
            skill = await memory_service.get_skill(db, uuid.UUID(skill_id))
            if skill is None:
                await _set_status("error", "技能不存在")
                return

            try:
                result = await asyncio.wait_for(
                    run_evolution(db, skill),
                    timeout=settings.skill_evolution_run_timeout,
                )
            except (asyncio.TimeoutError, EvolutionGateFailure) as exc:
                await _set_status("error", f"演化超时或未通过门禁: {exc}")
                return

            proposal = SkillProposal(
                skill_id=skill.id,
                proposed_content=result.proposed_content,
                rationale=result.rationale,
                eval_score_before=result.eval_score_before,
                eval_score_after=result.eval_score_after,
                diff_ratio=result.diff_ratio,
                dataset_summary=result.dataset_summary,
                status="pending",
            )
            db.add(proposal)
            await db.flush()
            # Auto-apply ("Self-improvement review"): when auto mode is on AND
            # the real (LLM) optimizer produced this proposal, approve it
            # immediately through the SINGLE write-back path (AgentSkill.content
            # + hermes SKILL.md sync). Gated on skill_evolution_enabled so the
            # LLM-free stub's placeholder proposals never auto-apply.
            if settings.evolution_auto_enabled and settings.skill_evolution_enabled:
                from app.services.skill_evolution_service import review_proposal
                await review_proposal(
                    db, proposal, reviewer_id=None, status="approved",
                    review_note="auto-applied",
                )
                logger.info(
                    "auto-evolution: skill %s proposal auto-applied (score %.2f→%.2f)",
                    skill_id[:8], result.eval_score_before, result.eval_score_after,
                )
            else:
                await db.commit()

        await _set_status("done")
    except Exception as exc:  # noqa: BLE001
        logger.exception("skill_evolution failed for skill %s", skill_id[:8])
        await _set_status("error", f"运行失败: {type(exc).__name__}")
