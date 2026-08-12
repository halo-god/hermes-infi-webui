"""Background Docling upgrade for knowledge base items.

Triggered after a knowledge upload: re-extracts the document with Docling
(high-quality Markdown with tables/OCR) and updates the content + status.
The record is already usable with fast-extracted content (pymupdf/python-docx)
from the upload endpoint — this just upgrades quality asynchronously.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app.db.base import async_session_maker
from app.db.models.team import TeamKnowledge
from app.core.docling_converter import convert_bytes_to_markdown_sync
from app.core import object_storage

logger = logging.getLogger("hermes.runner")


async def handle_docling_upgrade(task: dict) -> None:
    """Re-extract a knowledge item with Docling and update its content."""
    knowledge_id = task.get("knowledge_id")
    ext = task.get("ext", "")
    if not knowledge_id:
        return

    try:
        async with async_session_maker() as db:
            k = await db.get(TeamKnowledge, uuid.UUID(knowledge_id))
            if k is None:
                logger.warning("Docling upgrade: knowledge %s not found", knowledge_id)
                return
            if not k.storage_key:
                logger.warning("Docling upgrade: knowledge %s has no storage_key", knowledge_id)
                k.processing_status = "ready"
                await db.commit()
                return

            # Fetch raw bytes from object storage.
            raw = await asyncio.to_thread(object_storage.get, k.storage_key)
            if isinstance(raw, str):
                raw = raw.encode("utf-8")

            # Run Docling (CPU-bound, in a thread).
            md = await asyncio.to_thread(convert_bytes_to_markdown_sync, raw, ext)
            if md and md.strip():
                k.content = md
                logger.info("Docling upgrade: knowledge %s updated (%d chars)", knowledge_id[:8], len(md))
                # Content changed — rebuild the vector index so retrieval stays
                # consistent with what the UI shows. Best-effort: a failure here
                # must not flip the status to error (chunks-count=0 stays the
                # visible signal), so it's wrapped separately.
                try:
                    from app.services import rag_service
                    await rag_service.index_knowledge(db, k.id)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Docling upgrade: re-index failed for knowledge %s", knowledge_id, exc_info=True,
                    )
            else:
                # Docling failed — keep the fast-extracted content from upload.
                logger.info("Docling upgrade: knowledge %s — Docling returned nothing, keeping fast content", knowledge_id[:8])

            k.processing_status = "ready"
            await db.commit()

    except Exception:  # noqa: BLE001
        logger.exception("Docling upgrade failed for knowledge %s", knowledge_id)
        try:
            async with async_session_maker() as db:
                k = await db.get(TeamKnowledge, uuid.UUID(knowledge_id))
                if k:
                    # The upload already stored fast-extracted content and
                    # indexed it — a transient Docling failure must NOT flip
                    # the row to error (UI shows "解析失败" + retry button for
                    # a file that is actually usable). Only mark error when
                    # there is NO usable content at all.
                    if not (k.content or "").strip():
                        k.processing_status = "error"
                        await db.commit()
        except Exception:
            pass


async def handle_workspace_docling_upgrade(task: dict) -> None:
    """Background Docling upgrade for a conversation workspace file (pptx).

    The upload pipeline keeps the fast python-pptx HTML extraction in
    `content` (powers the workspace preview, images inline); this task writes
    the higher-quality Markdown into `content_md`, which the attachment
    resolver prefers for AI prompt injection. Preview is untouched."""
    file_id = task.get("file_id")
    ext = task.get("ext", "")
    if not file_id:
        return
    from app.db.models.workspace import WorkspaceFile

    try:
        async with async_session_maker() as db:
            f = await db.get(WorkspaceFile, uuid.UUID(file_id))
            if f is None:
                logger.warning("Docling upgrade: workspace file %s not found", file_id)
                return
            if not f.storage_key:
                logger.warning("Docling upgrade: workspace file %s has no storage_key", file_id)
                return

            raw = await asyncio.to_thread(object_storage.get, f.storage_key)
            if isinstance(raw, str):
                raw = raw.encode("utf-8")

            md = await asyncio.to_thread(convert_bytes_to_markdown_sync, raw, ext)
            if md and md.strip():
                f.content_md = md
                logger.info(
                    "Docling upgrade: workspace file %s updated (%d chars)",
                    file_id[:8], len(md),
                )
            else:
                logger.info(
                    "Docling upgrade: workspace file %s — Docling returned nothing, "
                    "keeping fast content", file_id[:8],
                )
            await db.commit()

        try:
            from app.core import redis as redis_core
            await redis_core.publish_event(
                task.get("conversation_id") or "",
                {
                    "type": "file",
                    "conversation_id": task.get("conversation_id") or "",
                    "file_id": str(file_id),
                    "name": task.get("name") or "",
                    "kind": ext,
                    "status": "ready",
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("failed to publish docling-upgrade event", exc_info=True)

    except Exception:  # noqa: BLE001
        logger.exception("Docling upgrade failed for workspace file %s", file_id)
