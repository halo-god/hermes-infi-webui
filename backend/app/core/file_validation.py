"""P2-security: magic-number file type validation.

Uploads were previously trusted by extension only (`name.rsplit(".", 1)`),
so an attacker could upload a .exe renamed to .pdf. This module cross-checks
the declared extension against the file's actual magic bytes via libmagic.

Design:
- `validate_upload(raw, ext)` raises HTTP 415 if the magic-detected MIME is
  incompatible with the declared extension. Returns the detected MIME on success.
- A single ALLOWED map declares which MIME patterns are acceptable per ext
  family. Office formats (docx/xlsx/pptx) are ZIP containers, so their magic
  is "application/zip" — handled specially.
- Plain-text files have no magic bytes (libmagic sniffs heuristics), so they
  get a relaxed check: any text/* MIME passes.
- If python-magic isn't installed, validation is skipped with a warning
  (graceful degradation — never block uploads on a missing optional dep).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Extension family → acceptable MIME prefixes. An upload passes if the
# magic-detected MIME starts with any of its family's prefixes.
_EXT_MIME_MAP: dict[str, tuple[str, ...]] = {
    # Images
    "png": ("image/png",),
    "jpg": ("image/jpeg",),
    "jpeg": ("image/jpeg",),
    "gif": ("image/gif",),
    "webp": ("image/webp",),
    "bmp": ("image/bmp",),
    "svg": ("image/svg", "text/xml", "text/plain"),
    # Documents (OOXML are ZIP containers)
    "pdf": ("application/pdf",),
    "docx": ("application/zip", "application/vnd.openxmlformats",),
    "xlsx": ("application/zip", "application/vnd.openxmlformats",),
    "pptx": ("application/zip", "application/vnd.openxmlformats",),
    "doc": ("application/msword", "application/x-ole-storage",),
    "xls": ("application/vnd.ms-excel", "application/x-ole-storage",),
    "ppt": ("application/vnd.ms-powerpoint", "application/x-ole-storage",),
    "rtf": ("application/rtf", "text/rtf", "text/plain"),
    # Data
    "csv": ("text/csv", "text/plain", "application/csv"),
    "json": ("application/json", "text/plain", "text/json"),
    # Archives
    "zip": ("application/zip", "application/x-zip-compressed"),
    "tar": ("application/x-tar",),
    "gz": ("application/gzip", "application/x-gzip"),
    # Audio/Video
    "mp3": ("audio/mpeg", "audio/mp3"),
    "mp4": ("video/mp4",),
    "wav": ("audio/wav", "audio/x-wav"),
}

# Text-like extensions: libmagic returns text/plain or text/* for these.
_TEXT_EXTS = frozenset({
    "md", "txt", "html", "htm", "js", "ts", "py", "go", "rs", "yaml", "yml",
    "toml", "sh", "bash", "log", "xml", "css", "diff", "patch", "sql",
})

_MAGIC = None
_MAGIC_AVAILABLE = False

try:
    import magic  # type: ignore[import-untyped]
    _MAGIC_AVAILABLE = True
except ImportError:
    logger.info("python-magic not installed — file type validation disabled")


def _get_magic():
    global _MAGIC
    if _MAGIC is None:
        # mime=True returns MIME strings ("application/pdf") instead of
        # human descriptions ("PDF document").
        _MAGIC = magic.Magic(mime=True)
    return _MAGIC


def detect_mime(raw: bytes) -> str | None:
    """Return the libmagic-detected MIME type, or None if unavailable."""
    if not _MAGIC_AVAILABLE:
        return None
    try:
        return _get_magic().from_buffer(raw[:4096])  # first 4KB is enough
    except Exception:  # noqa: BLE001 — never break on magic failure
        logger.debug("magic detection failed", exc_info=True)
        return None


def validate_upload(raw: bytes, ext: str) -> str | None:
    """Validate that the file's magic bytes match its declared extension.

    Returns the detected MIME on success. Raises HTTP 415 on mismatch.
    If python-magic is unavailable, returns None (validation skipped).
    """
    detected = detect_mime(raw)
    if detected is None:
        return None  # magic unavailable — skip

    ext = ext.lower().lstrip(".")

    # Text files: any text/* MIME is fine (no strict magic for plain text).
    if ext in _TEXT_EXTS:
        if detected.startswith("text/") or detected in ("application/json", "application/xml"):
            return detected
        # Some text files (e.g. empty .txt) detect as application/x-empty or
        # inode/x-empty — accept those too.
        if "empty" in detected:
            return detected
        raise HTTPException(
            status_code=415,
            detail=f"文件扩展名 .{ext} 与实际内容({detected})不符",
        )

    # Known binary extensions: check against the allow-list.
    if ext in _EXT_MIME_MAP:
        allowed = _EXT_MIME_MAP[ext]
        if any(detected.startswith(a) or detected == a for a in allowed):
            return detected
        raise HTTPException(
            status_code=415,
            detail=f"文件扩展名 .{ext} 与实际内容({detected})不符，可能被伪装",
        )

    # Unknown extension but we have magic data: allow if MIME looks benign.
    # Block known-dangerous executable types outright.
    _DANGEROUS = (
        "application/x-executable", "application/x-dosexec",
        "application/x-msdos-program", "application/x-sharedlib",
        "application/x-mach-binary",
    )
    if detected in _DANGEROUS:
        raise HTTPException(
            status_code=415,
            detail=f"不允许上传可执行文件({detected})",
        )

    return detected
