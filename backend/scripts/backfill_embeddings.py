#!/usr/bin/env python3
"""One-shot backfill: embed every existing team_knowledge row for P1-1 RAG.

Run AFTER applying migration 0057 and enabling rag_enabled in settings:
    cd backend && .venv/bin/python scripts/backfill_embeddings.py

Safe to re-run — index_knowledge() is idempotent (deletes old chunks first).
Skips items with empty content (folder placeholders, upload-in-progress).
Reports a per-item summary at the end.
"""
import asyncio
import logging
import sys

from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger("backfill_embeddings")


async def main() -> int:
    # Import after logging is configured so model-load messages show up.
    from app.config import settings
    if not settings.rag_enabled:
        logger.error("rag_enabled is False — set it to True before backfilling.")
        return 1

    from app.db.base import async_session_maker
    from app.db.models.team import TeamKnowledge
    from app.services import rag_service

    async with async_session_maker() as db:
        rows = (await db.execute(
            select(TeamKnowledge.id, TeamKnowledge.name)
            .where(TeamKnowledge.is_folder.is_(False))
            .order_by(TeamKnowledge.created_at)
        )).all()

    total = len(rows)
    logger.info("Found %s knowledge items to backfill", total)
    indexed = 0
    skipped = 0
    failed = 0

    for i, (kid, name) in enumerate(rows, 1):
        async with async_session_maker() as db:
            try:
                n = await rag_service.index_knowledge(db, kid)
                if n > 0:
                    indexed += 1
                    logger.info("[%s/%s] ✓ %s (%s) → %s chunks", i, total, name, kid, n)
                else:
                    skipped += 1
                    logger.info("[%s/%s] - %s (%s) → 0 chunks (empty or unavailable)", i, total, name, kid)
            except Exception:  # noqa: BLE001
                failed += 1
                logger.exception("[%s/%s] ✗ %s (%s) failed", i, total, name, kid)

    logger.info(
        "Backfill complete: %s indexed, %s skipped, %s failed (of %s total)",
        indexed, skipped, failed, total,
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
