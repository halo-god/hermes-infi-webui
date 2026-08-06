"""P3 cross-encoder rerank for RAG retrieval.

A small local CrossEncoder (default BAAI/bge-reranker-base, ~110MB) scores
(query, chunk) pairs — more accurate than pure cosine distance. Loaded
lazily as a process-wide singleton (mirrors app/core/embedding.py) and run in
a worker thread since sentence-transformers is CPU-bound.

Every failure path degrades to the retrieval-order shortlist — rerank must
never break a chat request.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_model = None
_init_lock = asyncio.Lock()


class RerankUnavailable(Exception):
    """Raised when the reranker model cannot be loaded (missing weights)."""


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import CrossEncoder
            _model = CrossEncoder(settings.rag_rerank_model, max_length=512)
        except Exception as exc:  # noqa: BLE001
            raise RerankUnavailable(f"reranker load failed: {exc}") from exc
    return _model


def _score_sync(pairs: list[tuple[str, str]]) -> list[float]:
    model = _get_model()
    return [float(s) for s in model.predict(pairs, show_progress_bar=False)]


async def rerank(query: str, hits: list, top_k: int) -> list:
    """Rerank retrieval hits by (query, chunk-content) cross-encoding.

    `hits` items must expose `.content`. Returns the top_k items reordered by
    descending relevance score, or the original order if the model is
    unavailable (caller treats it as best-effort).
    """
    if not hits or top_k <= 0:
        return hits[:top_k]
    pairs = [(query, h.content) for h in hits]
    try:
        scores = await asyncio.to_thread(_score_sync, pairs)
    except Exception:  # noqa: BLE001
        logger.warning("Rerank unavailable — using retrieval order", exc_info=True)
        return hits[:top_k]
    ranked = sorted(zip(hits, scores), key=lambda item: item[1], reverse=True)
    return [h for h, _ in ranked[:top_k]]
