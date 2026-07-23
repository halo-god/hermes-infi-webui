"""P2-file: Docling unified document parser.

Replaces the hand-written per-format extractors (python-docx/openpyxl/pymupdf)
with IBM's Docling for PDF/DOCX/PPTX/HTML. Docling produces clean structured
Markdown (tables preserved as MD tables, headings as #, lists as -), and has
built-in OCR for scanned PDFs — capabilities the old extractors lacked.

Lazy-loaded singleton: the Docling models (~200MB, ~1GB with OCR) are fetched
on first use and kept resident. Conversion is CPU/GPU-bound so callers wrap it
in asyncio.to_thread.

Graceful degradation: if Docling isn't installed or a conversion fails, the
caller (files.process_upload) falls back to the legacy extractors. This module
NEVER raises into the upload path — it returns None on any failure.

Supported by Docling: pdf, docx, pptx, html, htm, md, txt, json, xml, image
(for OCR). xlsx/csv stay on openpyxl (Docling is weak on pure data tables).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Extensions Docling handles well (structured docs with layout/tables).
_DOCLING_EXTS = frozenset({"pdf", "docx", "pptx", "html", "htm"})

_converter = None
_available: bool | None = None


def is_supported(ext: str) -> bool:
    """True if Docling can parse this extension (and would do better than the
    legacy extractors)."""
    return ext.lower().lstrip(".") in _DOCLING_EXTS


def _get_converter():
    """Lazy-init the Docling DocumentConverter singleton."""
    global _converter, _available
    if _converter is not None:
        return _converter
    if _available is False:
        return None
    try:
        from docling.document_converter import DocumentConverter
        logger.info("Loading Docling converter (first use, models may download)...")
        _converter = DocumentConverter()
        _available = True
        logger.info("Docling converter ready")
        return _converter
    except ImportError:
        logger.info("Docling not installed — falling back to legacy extractors")
        _available = False
        return None
    except Exception:  # noqa: BLE001 — model download/init failure
        logger.warning("Docling init failed — falling back to legacy extractors", exc_info=True)
        _available = False
        return None


def convert_to_markdown_sync(file_path: str) -> str | None:
    """Convert a file to Markdown using Docling. Sync — caller must run off
    the event loop. Returns None on any failure (caller falls back)."""
    conv = _get_converter()
    if conv is None:
        return None
    try:
        result = conv.convert(file_path)
        md = result.document.export_to_markdown()
        if md and md.strip():
            return md.strip()
        return None
    except Exception:  # noqa: BLE001 — never propagate; caller falls back
        logger.debug("Docling conversion failed for %s", file_path, exc_info=True)
        return None


async def convert_to_markdown(file_path: str) -> str | None:
    """Async wrapper — runs the CPU-bound conversion in a thread."""
    import asyncio
    return await asyncio.to_thread(convert_to_markdown_sync, file_path)


def convert_bytes_to_markdown_sync(raw: bytes, ext: str) -> str | None:
    """Convert raw bytes to Markdown. Writes to a temp file first (Docling
    needs a path). Sync — caller must run off the event loop."""
    import tempfile
    ext = ext.lower().lstrip(".")
    # Write to a named temp file with the right extension so Docling detects
    # the format correctly.
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        return convert_to_markdown_sync(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
