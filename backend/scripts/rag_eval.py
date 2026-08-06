"""RAG retrieval quality evaluation (P3).

Runs a small question/expected-keyword test set through the real retrieval
pipeline (pgvector, optional hybrid/rerank) against a team's knowledge items
and reports hit rate + mean reciprocal rank. Use it to calibrate
rag_min_score and to guard against retrieval regressions after chunking or
embedding changes.

Usage (from backend/):
    .venv/bin/python scripts/rag_eval.py <team_id> [--knowledge-dir <folder_id>] [--cases path/to/cases.json] [--min-score 0.35]

Test-set format (JSON):
    [
      {"q": "知识库里有关于数据库迁移的记录吗？", "expect": ["迁移", "alembic"]},
      {"q": "怎么部署沙箱？", "expect": ["bubblewrap", "沙箱"]}
    ]
A hit = any expected keyword appears in the retrieved top-k chunk texts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def _run(team_id: str, folder_id: str | None, cases_path: str, min_score: float, top_k: int) -> int:
    from app.db.base import async_session_maker
    from app.db.models.team import TeamKnowledge
    from app.services import rag_service
    from sqlalchemy import select

    team_uuid = uuid.UUID(team_id)
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    if not cases:
        print("测试集为空")
        return 1

    async with async_session_maker() as db:
        # Collect knowledge ids under the team (optionally scoped to a folder).
        stmt = select(TeamKnowledge.id).where(
            TeamKnowledge.team_id == team_uuid,
            TeamKnowledge.is_folder.is_(False),
        )
        if folder_id:
            stmt = stmt.where(TeamKnowledge.folder_id == uuid.UUID(folder_id))
        kid_rows = (await db.execute(stmt)).scalars().all()
        knowledge_ids = [uuid.UUID(str(k)) for k in kid_rows]
        # Only indexed items participate.
        indexed = [kid for kid in knowledge_ids if await rag_service.is_indexed(db, knowledge_id=kid)]
        if not indexed:
            print(f"该团队下没有已索引的知识条目（共 {len(knowledge_ids)} 条）")
            return 1
        print(f"参与评估的已索引条目: {len(indexed)} / {len(knowledge_ids)}，top_k={top_k}，min_score={min_score}")

        from app.config import settings
        prev = settings.rag_min_score
        settings.rag_min_score = min_score
        try:
            hits_all = 0
            rr_sum = 0.0
            per_case: list[dict] = []
            for case in cases:
                q = case["q"]
                expect = [str(e) for e in case.get("expect", [])]
                hits = await rag_service.search(db, q, indexed, top_k=top_k)
                texts = [h.content for h in hits]
                # hit if any expected keyword appears in any retrieved chunk
                matched = [e for e in expect if any(e in t for t in texts)]
                hit = len(matched) > 0
                rr = 0.0
                if hit:
                    # reciprocal rank: first position containing a match
                    for rank, t in enumerate(texts, start=1):
                        if any(e in t for e in matched):
                            rr = 1.0 / rank
                            break
                hits_all += 1 if hit else 0
                rr_sum += rr
                per_case.append({
                    "q": q[:60], "hit": hit, "rr": round(rr, 3),
                    "matched": matched, "top": [t[:40].replace("\n", " ") for t in texts[:3]],
                })
            n = len(cases)
            print(f"\n命中率: {hits_all}/{n} = {hits_all / n:.1%}  MRR: {rr_sum / n:.3f}")
            print("-" * 60)
            for c in per_case:
                mark = "✓" if c["hit"] else "✗"
                print(f"{mark} [{c['rr']:.2f}] {c['q']}  匹配: {c['matched']}")
                for t in c["top"]:
                    print(f"      · {t}")
            return 0 if hits_all / n >= 0.7 else 2
        finally:
            settings.rag_min_score = prev


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG retrieval quality evaluation")
    parser.add_argument("team_id", help="团队 UUID")
    parser.add_argument("--knowledge-dir", default=None, help="限定某个知识文件夹 UUID")
    parser.add_argument("--cases", default=str(Path(__file__).with_name("rag_eval_cases.json")))
    parser.add_argument("--min-score", type=float, default=0.35)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    return asyncio.run(_run(args.team_id, args.knowledge_dir, args.cases, args.min_score, args.top_k))


if __name__ == "__main__":
    raise SystemExit(main())
