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
            else:
                # Docling failed — keep the fast-extracted content from upload.
                logger.info("Docling upgrade: knowledge %s — Docling returned nothing, keeping fast content", knowledge_id[:8])

            k.processing_status = "ready"
            await db.commit()

    except Exception:  # noqa: BLE001 — mark error so the UI can show it
        logger.exception("Docling upgrade failed for knowledge %s", knowledge_id)
        try:
            async with async_session_maker() as db:
                k = await db.get(TeamKnowledge, uuid.UUID(knowledge_id))
                if k:
                    k.processing_status = "error"
                    await db.commit()
        except Exception:
            pass
