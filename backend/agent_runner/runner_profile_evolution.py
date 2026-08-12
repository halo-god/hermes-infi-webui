"""P2-4: runs a profile-prompt evolution pass and files the result as a
ProfilePromptProposal. Mirrors runner_skill_evolution. Never writes to
Profile.system_prompt directly — approval is the only path that does."""
from __future__ import annotations
import asyncio

import json
import logging
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.core import redis as R
from app.db.base import async_session_maker
from app.db.models.agent import Profile
from app.db.models.profile_evolution import ProfilePromptProposal
from skill_evolution.optimizer import EvolutionGateFailure
from skill_evolution.profile_optimizer import run_profile_evolution

logger = logging.getLogger("hermes.runner")


async def handle_profile_evolution(task: dict, agents: dict) -> None:
    """Handle a `profile_evolution` task: build the profile's eval dataset, run
    the optimizer, and store a pending ProfilePromptProposal if it clears gates."""
    profile_id = task["profile_id"]
    r = R.get_redis()
    status_key = f"profile_evolution:status:{profile_id}"

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
            profile = await db.get(Profile, uuid.UUID(profile_id))
            if profile is None:
                await _set_status("error", "助手不存在")
                return

            try:
                # Watchdog: GEPA can make up to 60 LLM calls (each now with a
                # per-call timeout) — cap the WHOLE run so a pathological
                # session can't occupy a runner slot forever.
                result = await asyncio.wait_for(
                    run_profile_evolution(db, profile),
                    timeout=settings.skill_evolution_run_timeout,
                )
            except (asyncio.TimeoutError, EvolutionGateFailure) as exc:
                await _set_status("error", f"演化超时或未通过门禁: {exc}")
                return

            proposal = ProfilePromptProposal(
                profile_id=profile.id,
                proposed_prompt=result.proposed_prompt,
                rationale=result.rationale,
                eval_score_before=result.eval_score_before,
                eval_score_after=result.eval_score_after,
                diff_ratio=result.diff_ratio,
                dataset_summary=result.dataset_summary,
                status="pending",
            )
            db.add(proposal)
            await db.flush()
            # Auto-apply: same gate as skill evolution — only the real LLM
            # optimizer's proposals are applied automatically (reviewer=None).
            if settings.evolution_auto_enabled and settings.skill_evolution_enabled:
                from app.services.profile_evolution_service import review_proposal
                await review_proposal(
                    db, proposal, reviewer_id=None, status="approved",
                    review_note="auto-applied",
                )
                logger.info(
                    "auto-evolution: profile %s proposal auto-applied (score %.2f→%.2f)",
                    profile_id[:8], result.eval_score_before, result.eval_score_after,
                )
            else:
                await db.commit()

        await _set_status("done")
    except Exception as exc:  # noqa: BLE001
        logger.exception("profile_evolution failed for profile %s", profile_id[:8])
        await _set_status("error", f"运行失败: {type(exc).__name__}")
