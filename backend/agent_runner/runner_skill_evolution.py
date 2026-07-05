"""Self-evolving skills: runs an evolution pass for one skill and files the
result as a SkillProposal (Stage D1 uses skill_evolution.optimizer's LLM-free
stub; Stage D2 swaps that stub for real DSPy+GEPA without touching this
file). Never writes to AgentSkill directly — approval (Stage E) is the only
path that does."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.skill_evolution import SkillProposal
from skill_evolution.optimizer import EvolutionGateFailure, run_evolution

logger = logging.getLogger("hermes.runner")


async def handle_skill_evolution(task: dict, agents: dict) -> None:
    """Handle a `skill_evolution` task: build the skill's eval dataset, run
    the optimizer, and store a pending SkillProposal if it clears the gates."""
    from app.services import memory_service

    skill_id = task["skill_id"]
    r = R.get_redis()
    status_key = R.skill_evolution_status_key(skill_id)

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
                result = await run_evolution(db, skill)
            except EvolutionGateFailure as exc:
                await _set_status("error", f"未通过门禁，未生成提案: {exc}")
                return

            db.add(SkillProposal(
                skill_id=skill.id,
                proposed_content=result.proposed_content,
                rationale=result.rationale,
                eval_score_before=result.eval_score_before,
                eval_score_after=result.eval_score_after,
                diff_ratio=result.diff_ratio,
                dataset_summary=result.dataset_summary,
                status="pending",
            ))
            await db.commit()

        await _set_status("done")
    except Exception as exc:  # noqa: BLE001
        logger.exception("skill_evolution failed for skill %s", skill_id[:8])
        await _set_status("error", f"运行失败: {type(exc).__name__}")
