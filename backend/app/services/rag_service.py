"""P1-1 RAG service: chunk knowledge docs, embed them, and retrieve by query.

Two public entry points:
  - index_knowledge(db, knowledge_id): called from team_service after a doc is
    uploaded or its content edited. Splits → embeds → replaces all chunks.
  - search(db, query, knowledge_ids, top_k): called from
    _build_knowledge_prompt when rag_enabled. pgvector cosine search, with
    optional hybrid (tsvector keyword) fusion and cross-encoder rerank.

Indexing is synchronous (the local model encodes ~10ms/chunk, so even a 50-page
doc finishes in a second or two) but never blocks the upload response for long.
Every failure path degrades: if the model is unavailable or pgvector is missing,
the caller falls back to legacy whole-doc injection.

P1-1+: chunks carry citation metadata (source_name / page / parent_title) so
the prompt can cite where a hit came from; search returns RagHit objects with
that metadata plus the vector distance. A similarity floor (rag_min_score)
drops unrelated hits. rag_hybrid fuses a tsvector keyword rank via RRF;
rag_rerank runs a cross-encoder over the shortlist (see app/core/reranker.py).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

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

# Markdown heading lines: "# Title" … "###### Title" (Docling output).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# Shortlist size for hybrid fusion / rerank: candidates before final top-k.
_SHORTLIST = 20


@dataclass
class ChunkPiece:
    """One chunk to embed: text plus the section heading it belongs to
    (parent-child chunking — the heading gives the chunk topical context)."""

    text: str
    parent_title: str | None = None


@dataclass
class RagHit:
    """One retrieved chunk with citation metadata for prompt annotation."""

    content: str
    distance: float  # pgvector cosine distance (0=identical, 2=opposite)
    source_name: str | None = None
    chunk_index: int = 0
    parent_title: str | None = None
    page: int | None = None
    # Hybrid/rerank score when those stages are enabled (None = pure vector).
    score: float | None = None


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


def _split_into_structured_chunks(
    text: str, size: int, overlap: int,
) -> list[ChunkPiece]:
    """P1-2 structure-aware chunking.

    Docling/plain-markdown content is split on heading lines ("#".."######"),
    then each section is sliding-window chunked. The first chunk of every
    section gets the heading prefixed (the title is a strong topical signal),
    and all chunks of the section record `parent_title` for citation.

    Content without headings falls back to the plain sliding window with
    parent_title=None — identical behaviour to the legacy chunker.
    """
    if not text or not text.strip():
        return []
    text = text.strip()
    has_headings = any(_HEADING_RE.match(ln.strip()) for ln in text.splitlines())
    if not has_headings:
        return [ChunkPiece(t) for t in _split_into_chunks(text, size, overlap)]

    pieces: list[ChunkPiece] = []
    current_title: str | None = None
    buffer: list[str] = []
    first_of_section = True

    def _flush() -> None:
        nonlocal buffer, first_of_section
        body = "\n".join(buffer).strip()
        buffer = []
        if not body:
            first_of_section = True
            return
        chunks = _split_into_chunks(body, size, overlap)
        for i, c in enumerate(chunks):
            if i == 0 and current_title and first_of_section:
                pieces.append(ChunkPiece(text=f"{current_title}\n{c}", parent_title=current_title))
            else:
                pieces.append(ChunkPiece(text=c, parent_title=current_title))
        first_of_section = False

    for line in text.splitlines():
        m = _HEADING_RE.match(line.strip())
        if m:
            _flush()
            current_title = m.group(2).strip()[:200] or None
            first_of_section = True
        else:
            buffer.append(line)
    _flush()
    return pieces or [ChunkPiece(text)]


async def _load_plain_content(db: AsyncSession, knowledge_id: uuid.UUID) -> tuple[str | None, str | None]:
    """Fetch the knowledge row's content + name, normalising content to plain
    text. Returns (content_or_None, name_or_None).

    Office docs are stored as HTML (see files.OFFICE_EXTRACTORS); we strip
    tags before chunking so embeddings reflect meaning, not markup. Plain-text
    / pdf docs are already clean.
    """
    k = await db.get(TeamKnowledge, knowledge_id)
    if k is None:
        return None, None
    content = k.content or ""
    if not content.strip():
        return None, k.name
    if "<" in content and ">" in content:
        content = _html_to_plain_text(content)
    return content.strip() or None, k.name


async def _load_project_doc_content(db: AsyncSession, project_doc_id: uuid.UUID) -> tuple[str | None, str | None]:
    """Fetch a project doc's content + name (mirrors _load_plain_content)."""
    from app.db.models.team import ProjectDoc
    d = await db.get(ProjectDoc, project_doc_id)
    if d is None:
        return None, None
    content = d.content or ""
    if not content.strip():
        return None, d.name
    if "<" in content and ">" in content:
        content = _html_to_plain_text(content)
    return content.strip() or None, d.name


async def _index_content(
    db: AsyncSession, content: str, *,
    knowledge_id: uuid.UUID | None = None, project_doc_id: uuid.UUID | None = None,
    source_name: str | None = None,
) -> int:
    """Shared core: split → embed → replace chunks for one source document.

    Exactly one of knowledge_id / project_doc_id must be set. Idempotent and
    never raises — returns 0 on any failure so the caller's upload/update still
    succeeds (the doc just won't be vector-retrievable). Re-indexing a doc
    whose chunks are unchanged skips re-embedding (cheap no-op).
    """
    if not await _rag_enabled(db):
        return 0

    # ── clear stale chunks when content is empty ──
    if not content or not content.strip():
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

    pieces = _split_into_structured_chunks(content, settings.rag_chunk_size, settings.rag_chunk_overlap)
    if not pieces:
        return 0

    # Idempotency: identical chunk text list → skip re-embedding. Still patch
    # metadata (source_name / parent_title) so legacy rows get backfilled.
    src_cond = (
        TeamKnowledgeChunk.knowledge_id == knowledge_id
        if knowledge_id else TeamKnowledgeChunk.project_doc_id == project_doc_id
    )
    existing = (await db.execute(
        select(TeamKnowledgeChunk.content, TeamKnowledgeChunk.chunk_index)
        .where(src_cond).order_by(TeamKnowledgeChunk.chunk_index)
    )).all()
    if [c.text for c in pieces] == [row[0] for row in existing]:
        for idx, piece in enumerate(pieces):
            await db.execute(
                TeamKnowledgeChunk.__table__.update()
                .where(src_cond, TeamKnowledgeChunk.chunk_index == idx)
                .values(source_name=source_name, parent_title=piece.parent_title)
            )
        await db.commit()
        return len(existing)

    try:
        vectors = await get_embedding_service().encode([p.text for p in pieces])
    except EmbeddingUnavailable:
        logger.warning("Embedding model unavailable — doc not indexed (kid=%s pdid=%s)", knowledge_id, project_doc_id)
        return 0
    except Exception:  # noqa: BLE001 — never block the upload on embedding
        logger.exception("Embedding failed (kid=%s pdid=%s)", knowledge_id, project_doc_id)
        return 0

    if len(vectors) != len(pieces):
        logger.error("Embedding count mismatch: %s chunks vs %s vectors", len(pieces), len(vectors))
        return 0

    # Replace atomically: delete old chunks for this source, insert new.
    await db.execute(delete(TeamKnowledgeChunk).where(src_cond))
    for idx, (piece, vec) in enumerate(zip(pieces, vectors, strict=True)):
        db.add(TeamKnowledgeChunk(
            knowledge_id=knowledge_id,
            project_doc_id=project_doc_id,
            chunk_index=idx,
            content=piece.text,
            source_name=source_name,
            parent_title=piece.parent_title,
            embedding=vec,
        ))
    await db.commit()
    src = f"knowledge {knowledge_id}" if knowledge_id else f"project_doc {project_doc_id}"
    logger.info("Indexed %s → %s chunks", src, len(pieces))
    return len(pieces)


async def index_knowledge(db: AsyncSession, knowledge_id: uuid.UUID) -> int:
    """Split, embed and store chunks for one team-knowledge item. See
    _index_content for the contract (idempotent, never raises)."""
    content, name = await _load_plain_content(db, knowledge_id)
    return await _index_content(db, content or "", knowledge_id=knowledge_id, source_name=name)


async def index_project_doc(db: AsyncSession, project_doc_id: uuid.UUID) -> int:
    """P2-file: same pipeline for project docs, which previously only got
    whole-doc injection (truncated to 2000 chars). Now they share the chunk
    table + embedding service."""
    content, name = await _load_project_doc_content(db, project_doc_id)
    return await _index_content(db, content or "", project_doc_id=project_doc_id, source_name=name)


async def count_chunks(db: AsyncSession, knowledge_id: uuid.UUID) -> int:
    """How many chunks are stored for an item — drives the "已索引 N 块" badge."""
    res = await db.execute(
        select(func.count()).select_from(TeamKnowledgeChunk)
        .where(TeamKnowledgeChunk.knowledge_id == knowledge_id)
    )
    return int(res.scalar() or 0)


async def is_indexed(
    db: AsyncSession,
    knowledge_id: uuid.UUID | None = None,
    project_doc_id: uuid.UUID | None = None,
) -> bool:
    """True iff the item has at least one chunk with a non-null embedding.

    Exactly one of knowledge_id / project_doc_id should be set; the
    knowledge_id position is kept for backward compatibility.
    """
    conds = []
    if knowledge_id is not None:
        conds.append(TeamKnowledgeChunk.knowledge_id == knowledge_id)
    if project_doc_id is not None:
        conds.append(TeamKnowledgeChunk.project_doc_id == project_doc_id)
    if not conds:
        return False
    res = await db.execute(
        select(func.count()).select_from(TeamKnowledgeChunk)
        .where(*conds)
        .where(TeamKnowledgeChunk.embedding.isnot(None))
    )
    return int(res.scalar() or 0) > 0


def _source_cond(
    knowledge_ids: list[uuid.UUID], project_doc_ids: list[uuid.UUID],
):
    """Build the chunk-source WHERE conditions (at least one list is non-empty)."""
    from sqlalchemy import or_
    conds = []
    if knowledge_ids:
        conds.append(TeamKnowledgeChunk.knowledge_id.in_(knowledge_ids))
    if project_doc_ids:
        conds.append(TeamKnowledgeChunk.project_doc_id.in_(project_doc_ids))
    return or_(*conds)


async def _hybrid_search(
    db: AsyncSession, query: str, source_cond, qvec, k: int,
) -> list[RagHit]:
    """P1-4: fuse the vector rank with a tsvector keyword rank (RRF).

    Requires the `content_tsv` generated column + GIN index from migration
    0083; if the column is missing (migration not run) this degrades to the
    pure vector path with a warning.
    """
    try:

        base_cols = (
            TeamKnowledgeChunk.content,
            TeamKnowledgeChunk.embedding.cosine_distance(qvec),
            TeamKnowledgeChunk.source_name,
            TeamKnowledgeChunk.chunk_index,
            TeamKnowledgeChunk.parent_title,
            TeamKnowledgeChunk.page,
        )
        # Vector shortlist.
        vec_stmt = (
            select(*base_cols)
            .where(source_cond)
            .where(TeamKnowledgeChunk.embedding.isnot(None))
            .order_by(TeamKnowledgeChunk.embedding.cosine_distance(qvec))
            .limit(_SHORTLIST)
        )
        vec_rows = (await db.execute(vec_stmt)).all()
        if not vec_rows:
            return []
    except Exception:  # noqa: BLE001
        logger.exception("Hybrid search setup failed — falling back to vector-only")
        return []

    # Keyword shortlist via tsvector match, ranked by ts_rank (a reasonable
    # BM25-style keyword relevance proxy).
    kw = query.strip().replace("?", " ").replace("？", " ")
    from sqlalchemy import bindparam

    ts_query = func.websearch_to_tsquery("simple", bindparam("kw"))
    kw_stmt = (
        select(*base_cols)
        .where(source_cond)
        .where(TeamKnowledgeChunk.content_tsv.op("@@")(ts_query))
        .order_by(func.ts_rank(TeamKnowledgeChunk.content_tsv, ts_query).desc())
        .limit(_SHORTLIST)
    )
    try:
        kw_rows = (await db.execute(kw_stmt, {"kw": kw})).all()
    except Exception:  # noqa: BLE001 — column/index missing (migration not run)
        logger.warning("Hybrid keyword search unavailable (migration 0083?) — vector-only")
        kw_rows = []

    def _to_hit(row) -> RagHit:
        return RagHit(
            content=row[0], distance=float(row[1]), source_name=row[2],
            chunk_index=row[3], parent_title=row[4], page=row[5],
        )

    # Reciprocal Rank Fusion: score = Σ 1/(60 + rank), dedup by chunk id.
    fused: dict[tuple[str, int], tuple[float, RagHit]] = {}
    for rank, row in enumerate(vec_rows, start=1):
        hit = _to_hit(row)
        key = (hit.source_name or "", hit.chunk_index)
        cur, _ = fused.get(key, (0.0, hit))
        fused[key] = (cur + 1.0 / (60.0 + rank), hit)
    for rank, row in enumerate(kw_rows, start=1):
        hit = _to_hit(row)
        key = (hit.source_name or "", hit.chunk_index)
        cur, existing = fused.get(key, (0.0, hit))
        fused[key] = (cur + 1.0 / (60.0 + rank), existing)

    ranked = sorted(fused.values(), key=lambda item: item[0], reverse=True)
    for score, hit in ranked[:k]:
        hit.score = score
    return [hit for _, hit in ranked[:k]]


async def _rerank_hits(db: AsyncSession, query: str, hits: list[RagHit], k: int) -> list[RagHit]:
    """P3: cross-encoder rerank of the shortlist. Best-effort — any failure
    falls back to the incoming (vector/hybrid) order."""
    try:
        from app.core.reranker import rerank
        ranked = await rerank(query, hits, top_k=k)
        return ranked or hits[:k]
    except Exception:  # noqa: BLE001
        logger.warning("Rerank failed — using retrieval order", exc_info=True)
        return hits[:k]


async def search(
    db: AsyncSession, query: str,
    knowledge_ids: list[uuid.UUID] | None = None,
    project_doc_ids: list[uuid.UUID] | None = None,
    top_k: int | None = None,
) -> list[RagHit]:
    """Retrieve the top-k relevant chunks for `query`.

    Searches across both team-knowledge chunks (knowledge_ids) and project-doc
    chunks (project_doc_ids). Pipeline:
      1. vector cosine search (pgvector <=>)
      2. similarity floor: distance <= 1 - rag_min_score (drops unrelated hits)
      3. optional hybrid fusion (tsvector keywords) when settings.rag_hybrid
      4. optional cross-encoder rerank when settings.rag_rerank
    Returns RagHit list with citation metadata. Embedding errors raise
    EmbeddingUnavailable — the caller must catch and fall back to legacy
    injection.
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
    source_cond = _source_cond(knowledge_ids, project_doc_ids)

    cols = (
        TeamKnowledgeChunk.content,
        TeamKnowledgeChunk.embedding.cosine_distance(qvec),
        TeamKnowledgeChunk.source_name,
        TeamKnowledgeChunk.chunk_index,
        TeamKnowledgeChunk.parent_title,
        TeamKnowledgeChunk.page,
    )

    hybrid_on = await _rag_flag(db, "hybrid", settings.rag_hybrid)
    rerank_on = await _rag_flag(db, "rerank", settings.rag_rerank)
    if hybrid_on:
        hits = await _hybrid_search(db, query, source_cond, qvec, k)
    else:
        stmt = (
            select(*cols)
            .where(source_cond)
            .where(TeamKnowledgeChunk.embedding.isnot(None))
            .order_by(TeamKnowledgeChunk.embedding.cosine_distance(qvec))
            .limit(_SHORTLIST if rerank_on else k)
        )
        res = await db.execute(stmt)
        hits = [
            RagHit(content=r[0], distance=float(r[1]), source_name=r[2],
                   chunk_index=r[3], parent_title=r[4], page=r[5])
            for r in res.all()
        ]

    # P1-3 similarity floor.
    max_dist = 1.0 - settings.rag_min_score
    hits = [h for h in hits if h.distance <= max_dist]

    if rerank_on:
        hits = await _rerank_hits(db, query, hits, k)
    return hits[:k]


async def _rag_enabled(db: AsyncSession) -> bool:
    """Effective RAG switch (DB override wins — see settings_service.rag_enabled)."""
    from app.services.settings_service import rag_enabled
    return await rag_enabled(db)


async def _rag_flag(db: AsyncSession, key: str, default: bool) -> bool:
    """Effective per-toggle RAG flag (DB override wins)."""
    from app.services.settings_service import rag_flag
    return await rag_flag(db, key, default)
