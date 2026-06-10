"""File-safety helpers: bounded upload reads + path-traversal confinement."""
from __future__ import annotations

import os

from fastapi import HTTPException, UploadFile

_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB


async def read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile fully, but abort with HTTP 413 once it exceeds max_bytes.

    Reads in chunks so an oversized upload can't balloon memory before the
    limit is hit (``await file.read()`` would buffer the whole body first).
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大，上限 {max_bytes // (1024 * 1024)}MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def safe_relative_path(name: str, fallback: str = "untitled.txt") -> str:
    """Normalize a user/agent-supplied path to a contained relative path.

    Anchors the path at root before normalizing so ``../`` segments can never
    climb above it (``a/../../b`` → ``b``, ``../../etc/passwd`` → ``etc/passwd``),
    then strips the leading separator. Never raises — preserves valid nested
    paths like ``src/main.py`` for folder support.
    """
    candidate = (name or "").replace("\\", "/").strip()
    normalized = os.path.normpath("/" + candidate).lstrip("/")
    return normalized.replace(os.sep, "/") or fallback


def confine_to_dir(base_dir: str, relative: str) -> str:
    """Join base_dir + a (pre-normalized) relative path and assert containment.

    Defense in depth after ``safe_relative_path``: resolves symlinks and rejects
    any result that escapes base_dir. Raises HTTP 400 on escape.
    """
    base_real = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base_real, relative))
    if target != base_real and not target.startswith(base_real + os.sep):
        raise HTTPException(status_code=400, detail="非法文件路径")
    return target
