"""P1-1 RAG service: chunk knowledge docs, embed them, and retrieve by query.

Two public entry points:
  - index_knowledge(db, knowledge_id): called from team_service after a doc is
    uploaded or its content edited. Splits → embeds → replaces all chunks.
  - search(db, query, knowledge_ids, top_k): called from
    _build_knowledge_prompt when rag_enabled. pgvector cosine search.

Indexing is synchronous (the local model encodes ~10ms/chunk, so even a 50-page
doc finishes in a second or two) but never blocks the upload response for long.
Every failure path degrades: if the model is unavailable or pgvector is missing,
the caller falls back to legacy whole-doc injection.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.embedding import EmbeddingUnavailable, get_embedding_service
from app.db.models.team import TeamKnowledge, TeamKnowledgeChunk

logger = logging.getLogger(__name__)

# Reuse the existing HTML→text stripper so chunks match what the legacy path
# would have injected (keeps "检索到的块" and "全量注入文本" token-comparable).
from app.services.conversation_service import _html_to_plain_text  # noqa: E402

# Minimum chunk length — anything shorter is noise (header fragments, blank
# table cells). Merged into neighbours during the sliding window.
_MIN_CHUNK_CHARS = 30


def _split_into_chunks(text: str, size: int, overlap: int) -> list[str]:
    """Sliding-window chunker sized in characters (CJK ≈ 2 chars/token, so
    size=500 ≈ 250 tokens, a comfortable retrieval granularity).

    Strips empty/whitespace-only slices and merges fragments below
    _MIN_CHUNK_CHARS into the previous chunk so we don't store throwaway rows.
    """
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    raw: list[str] = []
    for start in range(0, len(text), step):
        chunk = text[start:start + size]
        if chunk.strip():
            raw.append(chunk.strip())
        if start + size >= len(text):
            break
    # Merge tiny trailing fragments into the previous chunk.
    merged: list[str] = []
    for c in raw:
        if merged and len(c) < _MIN_CHUNK_CHARS:
            merged[-1] = merged[-1] + " " + c
        else:
            merged.append(c)
    return merged or [text]


async def _load_plain_content(db: AsyncSession, knowledge_id: uuid.UUID) -> str | None:
    """Fetch the knowledge row's content and normalise to plain text.

    Office docs are stored as HTML (see files.OFFICE_EXTRACTORS); we strip
    tags before chunking so embeddings reflect meaning, not markup. Plain-text
    / pdf docs are already clean.
    """
    k = await db.get(TeamKnowledge, knowledge_id)
    if k is None:
        return None
    content = k.content or ""
    if not content.strip():
        return None
    if "<" in content and ">" in content:
        content = _html_to_plain_text(content)
    return content.strip() or None


async def _load_project_doc_content(db: AsyncSession, project_doc_id: uuid.UUID) -> str | None:
    """Fetch a project doc's content and normalise to plain text (mirrors
    _load_plain_content but for ProjectDoc)."""
    from app.db.models.team import ProjectDoc
    d = await db.get(ProjectDoc, project_doc_id)
    if d is None:
        return None
    content = d.content or ""
    if not content.strip():
        return None
    if "<" in content and ">" in content:
        content = _html_to_plain_text(content)
    return content.strip() or None


async def _index_content(
    db: AsyncSession, content: str, *,
    knowledge_id: uuid.UUID | None = None, project_doc_id: uuid.UUID | None = None,
) -> int:
    """Shared core: split → embed → replace chunks for one source document.

    Exactly one of knowledge_id / project_doc_id must be set. Idempotent and
    never raises — returns 0 on any failure so the caller's upload/update still
    succeeds (the doc just won't be vector-retrievable).
    """
    if not settings.rag_enabled:
        return 0
    if not content or not content.strip():
        # Clear stale chunks for whichever source this is.
        conds = []
        if knowledge_id:
            conds.append(TeamKnowledgeChunk.knowledge_id == knowledge_id)
        if project_doc_id:
            conds.append(TeamKnowledgeChunk.project_doc_id == project_doc_id)
        if conds:
            from sqlalchemy import or_
            await db.execute(delete(TeamKnowledgeChunk).where(or_(*conds)))
            await db.commit()
        return 0

    chunks = _split_into_chunks(content, settings.rag_chunk_size, settings.rag_chunk_overlap)
    if not chunks:
        return 0

    try:
        vectors = await get_embedding_service().encode(chunks)
    except EmbeddingUnavailable:
        logger.warning("Embedding model unavailable — doc not indexed (kid=%s pdid=%s)", knowledge_id, project_doc_id)
        return 0
    except Exception:  # noqa: BLE001 — never block the upload on embedding
        logger.exception("Embedding failed (kid=%s pdid=%s)", knowledge_id, project_doc_id)
        return 0

    if len(vectors) != len(chunks):
        logger.error("Embedding count mismatch: %s chunks vs %s vectors", len(chunks), len(vectors))
        return 0

    # Replace atomically: delete old chunks for this source, insert new.
    if knowledge_id:
        await db.execute(delete(TeamKnowledgeChunk).where(TeamKnowledgeChunk.knowledge_id == knowledge_id))
    else:
        await db.execute(delete(TeamKnowledgeChunk).where(TeamKnowledgeChunk.project_doc_id == project_doc_id))
    for idx, (chunk_text, vec) in enumerate(zip(chunks, vectors, strict=True)):
        db.add(TeamKnowledgeChunk(
            knowledge_id=knowledge_id,
            project_doc_id=project_doc_id,
            chunk_index=idx,
            content=chunk_text,
            embedding=vec,
        ))
    await db.commit()
    src = f"knowledge {knowledge_id}" if knowledge_id else f"project_doc {project_doc_id}"
    logger.info("Indexed %s → %s chunks", src, len(chunks))
    return len(chunks)


async def index_knowledge(db: AsyncSession, knowledge_id: uuid.UUID) -> int:
    """Split, embed and store chunks for one team-knowledge item. See
    _index_content for the contract (idempotent, never raises)."""
    content = await _load_plain_content(db, knowledge_id)
    return await _index_content(db, content or "", knowledge_id=knowledge_id)


async def index_project_doc(db: AsyncSession, project_doc_id: uuid.UUID) -> int:
    """P2-file: same pipeline for project docs, which previously only got
    whole-doc injection (truncated to 2000 chars). Now they share the chunk
    table + embedding service."""
    content = await _load_project_doc_content(db, project_doc_id)
    return await _index_content(db, content or "", project_doc_id=project_doc_id)


async def count_chunks(db: AsyncSession, knowledge_id: uuid.UUID) -> int:
    """How many chunks are stored for an item — drives the "已索引 N 块" badge."""
    res = await db.execute(
        select(func.count()).select_from(TeamKnowledgeChunk)
        .where(TeamKnowledgeChunk.knowledge_id == knowledge_id)
    )
    return int(res.scalar() or 0)


async def is_indexed(db: AsyncSession, knowledge_id: uuid.UUID) -> bool:
    """True iff the item has at least one chunk with a non-null embedding."""
    res = await db.execute(
        select(func.count()).select_from(TeamKnowledgeChunk)
        .where(TeamKnowledgeChunk.knowledge_id == knowledge_id)
        .where(TeamKnowledgeChunk.embedding.isnot(None))
    )
    return int(res.scalar() or 0) > 0


async def search(
    db: AsyncSession, query: str,
    knowledge_ids: list[uuid.UUID] | None = None,
    project_doc_ids: list[uuid.UUID] | None = None,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """Vector search: embed the query, return the top-k (content, distance) pairs.

    Searches across both team-knowledge chunks (knowledge_ids) and project-doc
    chunks (project_doc_ids). distance is cosine (0=identical, 2=opposite) from
    pgvector's <=>. Empty id lists or a trivially-empty query returns [].
    Embedding errors raise EmbeddingUnavailable — the caller must catch and
    fall back to legacy injection.
    """
    knowledge_ids = knowledge_ids or []
    project_doc_ids = project_doc_ids or []
    if not (knowledge_ids or project_doc_ids) or not query or not query.strip():
        return []
    k = top_k or settings.rag_top_k
    try:
        qvecs = await get_embedding_service().encode([query.strip()])
    except EmbeddingUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingUnavailable(f"query embedding failed: {exc}") from exc
    if not qvecs:
        return []
    qvec = qvecs[0]
    from sqlalchemy import or_
    # pgvector cosine distance: <=> . Order ascending (nearest first).
    stmt = (
        select(TeamKnowledgeChunk.content, TeamKnowledgeChunk.embedding.cosine_distance(qvec))
        .where(or_(
            TeamKnowledgeChunk.knowledge_id.in_(knowledge_ids) if knowledge_ids else False,  # type: ignore[arg-type]
            TeamKnowledgeChunk.project_doc_id.in_(project_doc_ids) if project_doc_ids else False,  # type: ignore[arg-type]
        ))
        .where(TeamKnowledgeChunk.embedding.isnot(None))
        .order_by(TeamKnowledgeChunk.embedding.cosine_distance(qvec))
        .limit(k)
    )
    res = await db.execute(stmt)
    return [(row[0], float(row[1])) for row in res.all()]
