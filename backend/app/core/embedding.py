"""Process-local embedding service backed by a local sentence-transformers model.

Lazy-loaded singleton: the model is only fetched on first use (the runner
worker, which is the only caller) and then kept resident for the life of the
process. Encoding runs in a thread to avoid blocking the asyncio loop —
sentence-transformers is pure sync torch under the hood.

Why local (not an API): zero per-query cost, offline, no key to manage, and
~10ms/encode once warm. The trade-off is ~200MB on disk (torch CPU) + ~95MB
model weights, accepted per the P1-1 design decision.

If sentence-transformers is not installed (e.g. a stripped CI image), every
call raises EmbeddingUnavailable; rag_service must catch this and degrade to
the legacy whole-document injection rather than crashing the dispatch path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class EmbeddingUnavailable(RuntimeError):
    """Raised when the sentence-transformers model can't be loaded. Callers
    must treat this as "RAG is off" and fall back, never propagate it to the
    user-facing dispatch path."""


class EmbeddingService:
    """Singleton wrapper around SentenceTransformer.

    Not thread-safe for *initialisation* — the first concurrent callers could
    both instantiate the model. In practice the runner worker is single-loop
    asyncio so this never happens; the lock below guards the async entry
    point anyway. Subsequent calls reuse the resident instance.
    """

    _instance: "EmbeddingService | None" = None
    _model: "SentenceTransformer | None" = None
    _init_lock: asyncio.Lock | None = None

    def __new__(cls) -> "EmbeddingService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Test hook: drop the cached model so the next encode() reloads."""
        cls._instance = None
        cls._model = None
        cls._init_lock = None

    def _ensure_lock(self) -> asyncio.Lock:
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        return self._init_lock

    def _load_sync(self) -> "SentenceTransformer":
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "sentence-transformers is not installed; RAG is unavailable. "
                "Run pip install sentence-transformers or set rag_enabled=False."
            ) from exc
        model_name = settings.rag_embedding_model
        logger.info("Loading embedding model %s (first use, may take a moment)", model_name)
        # bge-small-zh-v1.5 recommends normalised embeddings for cosine
        # similarity — we normalise on encode() so raw dot-product == cosine.
        m = SentenceTransformer(model_name)
        self._model = m
        # sentence-transformers renamed this method in newer versions; support both.
        get_dim = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
        logger.info("Embedding model %s ready (dim=%s)", model_name, get_dim())
        return m

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of texts. Returns one 512-dim vector per text.

        Empty strings are legal and produce a zero vector (avoids NaN in
        cosine search); callers should filter trivially-empty chunks upstream.
        """
        if not texts:
            return []
        lock = self._ensure_lock()
        async with lock:
            model = self._model if self._model is not None else await asyncio.to_thread(self._load_sync)
        # Run the (sync, CPU-bound) encode off the event loop.
        vectors = await asyncio.to_thread(
            lambda: model.encode(
                texts,
                normalize_embeddings=True,  # cosine == dot product after norm
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        )
        return [v.tolist() for v in vectors]


def get_embedding_service() -> EmbeddingService:
    """Module-level accessor — returns the process singleton."""
    return EmbeddingService()
