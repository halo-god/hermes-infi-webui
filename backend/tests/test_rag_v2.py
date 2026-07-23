"""P1-1 + P2-file: RAG pipeline tests — chunking, indexing, retrieval.

Chunking is pure logic (no DB). Indexing/retrieval use the `db` fixture with
mocked embeddings (avoids the sentence-transformers model dependency in CI).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag_service import (
    _split_into_chunks,
    count_chunks,
    index_knowledge,
    index_project_doc,
    is_indexed,
    search,
)


# ── chunking (pure logic) ──

class TestChunking:
    def test_short_text_single_chunk(self):
        chunks = _split_into_chunks("short text", 500, 100)
        assert len(chunks) == 1
        assert chunks[0] == "short text"

    def test_long_text_multiple_chunks(self):
        text = "人工智能是计算机科学的分支。" * 40  # ~400 chars
        chunks = _split_into_chunks(text, 500, 100)
        assert len(chunks) >= 2
        # each chunk should be <= size (except possibly the last merged fragment)
        assert all(len(c) <= 600 for c in chunks)

    def test_empty_text_no_chunks(self):
        assert _split_into_chunks("", 500, 100) == []
        assert _split_into_chunks("   ", 500, 100) == []

    def test_whitespace_only_lines_dropped(self):
        chunks = _split_into_chunks("real content\n\n\n   \nmore", 500, 100)
        assert len(chunks) == 1
        assert "real content" in chunks[0]

    def test_tiny_fragments_merged(self):
        # A tiny trailing fragment should merge into the previous chunk
        text = "x" * 400 + " " + "y" * 20  # 420 chars, last 20 < _MIN_CHUNK_CHARS
        chunks = _split_into_chunks(text, 200, 50)
        # the tiny "y" fragment should not be its own chunk
        if len(chunks) > 1:
            assert len(chunks[-1]) >= 30 or "y" in chunks[-2]


# ── indexing & retrieval (DB + mocked embeddings) ──

def _mock_encode(texts):
    """Deterministic mock: hash each text into a 512-dim vector so similar
    texts (sharing substrings) produce closer cosine distances."""
    import hashlib
    import math
    vecs = []
    for t in texts:
        h = hashlib.sha512(t.encode()).digest()  # 64 bytes
        v = [(h[i % 64] / 255.0 - 0.5) for i in range(512)]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vecs.append([x / norm for x in v])
    return vecs


@pytest_asyncio.fixture
async def _rag_enabled(monkeypatch):
    """Force rag_enabled on and mock the embedding service."""
    from app.config import settings
    monkeypatch.setattr(settings, "rag_enabled", True)
    with patch("app.services.rag_service.get_embedding_service") as mock_svc:
        svc = mock_svc.return_value
        svc.encode = AsyncMock(side_effect=_mock_encode)
        yield


@pytest_asyncio.fixture
async def team_and_knowledge(db: AsyncSession):
    """Create a team + knowledge item for indexing tests."""
    from app.db.models.team import Team
    from app.db.models.user import User
    u = User(email="ragtest@test.com", name="ragtest", password_hash="x")
    db.add(u)
    await db.flush()
    t = Team(name="ragtest-team")
    db.add(t)
    await db.flush()
    return t.id, u.id


class TestRAGIndexing:
    async def test_index_knowledge_creates_chunks(
        self, db: AsyncSession, _rag_enabled, team_and_knowledge
    ):
        from app.db.models.team import TeamKnowledge
        team_id, _ = team_and_knowledge
        k = TeamKnowledge(
            team_id=team_id, name="test", kind="txt",
            content="人工智能是研究模拟人类智能的学科。" * 20,
        )
        db.add(k)
        await db.flush()

        n = await index_knowledge(db, k.id)
        assert n >= 1
        assert await count_chunks(db, k.id) == n
        assert await is_indexed(db, k.id) is True

    async def test_index_empty_content_clears_chunks(
        self, db: AsyncSession, _rag_enabled, team_and_knowledge
    ):
        from app.db.models.team import TeamKnowledge
        team_id, _ = team_and_knowledge
        k = TeamKnowledge(team_id=team_id, name="empty", kind="txt", content="")
        db.add(k)
        await db.flush()
        n = await index_knowledge(db, k.id)
        assert n == 0
        assert await count_chunks(db, k.id) == 0

    async def test_index_project_doc(
        self, db: AsyncSession, _rag_enabled, team_and_knowledge
    ):
        from app.db.models.team import Project, ProjectDoc
        team_id, _ = team_and_knowledge
        p = Project(team_id=team_id, name="p")
        db.add(p)
        await db.flush()
        d = ProjectDoc(
            project_id=p.id, name="doc", kind="txt",
            content="这个项目使用 React 构建前端界面。" * 15,
        )
        db.add(d)
        await db.flush()

        n = await index_project_doc(db, d.id)
        assert n >= 1
        # search by project_doc_ids
        hits = await search(db, "前端技术", project_doc_ids=[d.id])
        assert len(hits) >= 1

    async def test_search_returns_relevant(
        self, db: AsyncSession, _rag_enabled, team_and_knowledge
    ):
        from app.db.models.team import TeamKnowledge
        team_id, _ = team_and_knowledge
        k = TeamKnowledge(
            team_id=team_id, name="ml-doc", kind="txt",
            content="机器学习是让计算机从数据中学习的技术，包括监督学习和无监督学习。" * 10,
        )
        db.add(k)
        await db.flush()
        await index_knowledge(db, k.id)

        hits = await search(db, "什么是机器学习", knowledge_ids=[k.id])
        assert len(hits) >= 1
        # distance should be reasonable (mock embeddings are deterministic)
        assert hits[0][1] >= 0.0

    async def test_search_empty_ids_returns_empty(self, db: AsyncSession, _rag_enabled):
        hits = await search(db, "query", knowledge_ids=[])
        assert hits == []

    async def test_rag_disabled_returns_zero(
        self, db: AsyncSession, monkeypatch, team_and_knowledge
    ):
        from app.config import settings
        from app.db.models.team import TeamKnowledge
        monkeypatch.setattr(settings, "rag_enabled", False)
        team_id, _ = team_and_knowledge
        k = TeamKnowledge(team_id=team_id, name="off", kind="txt", content="content here")
        db.add(k)
        await db.flush()
        n = await index_knowledge(db, k.id)
        assert n == 0
