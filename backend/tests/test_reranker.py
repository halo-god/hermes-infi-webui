"""Reranker tests — best-effort degradation must never break retrieval."""
from __future__ import annotations

import pytest

from app.core import reranker


class Hit:
    def __init__(self, content: str):
        self.content = content


@pytest.mark.asyncio
async def test_rerank_empty_hits_returns_empty():
    assert await reranker.rerank("q", [], 5) == []


@pytest.mark.asyncio
async def test_rerank_top_k_zero_returns_nothing():
    hits = [Hit("a"), Hit("b")]
    assert await reranker.rerank("q", hits, 0) == []
    assert await reranker.rerank("q", [], -1) == []


@pytest.mark.asyncio
async def test_rerank_reorders_by_descending_score(monkeypatch):
    def fake_score(pairs):
        # Reverse relevance: longer content scores higher.
        return [float(len(p[1])) for p in pairs]

    monkeypatch.setattr(reranker, "_score_sync", fake_score)
    hits = [Hit("short"), Hit("a much longer chunk of content")]
    out = await reranker.rerank("q", hits, 2)
    assert [h.content for h in out] == [hits[1].content, hits[0].content]


@pytest.mark.asyncio
async def test_rerank_respects_top_k(monkeypatch):
    monkeypatch.setattr(
        reranker,
        "_score_sync",
        lambda pairs: [float(i) for i in range(len(pairs))],
    )
    hits = [Hit(f"c{i}") for i in range(5)]
    out = await reranker.rerank("q", hits, 2)
    assert len(out) == 2
    # Highest scores (4, 3) come first.
    assert out[0].content == "c4"


@pytest.mark.asyncio
async def test_rerank_degrades_to_original_order_on_model_failure(monkeypatch):
    def boom(pairs):
        raise reranker.RerankUnavailable("weights missing")

    monkeypatch.setattr(reranker, "_score_sync", boom)
    hits = [Hit("a"), Hit("b")]
    out = await reranker.rerank("q", hits, 5)
    assert [h.content for h in out] == ["a", "b"]
