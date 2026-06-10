"""Workspace file persistence for produced artifacts.

Two backends (STORAGE_BACKEND):
  - db    : content stored inline in Postgres (default; zero extra infra)
  - minio : content offloaded to MinIO/S3, only a storage_key kept in PG

Either way the API serves content via read_content().
"""
from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import select

from app.config import settings
from app.core import object_storage
from app.core.files import safe_relative_path
from app.db.base import async_session_maker
from app.db.models.workspace import WorkspaceFile, WorkspaceFileVersion

_KIND_BY_EXT = {
    ".md": "md", ".markdown": "md", ".docx": "docx", ".doc": "docx",
    ".csv": "csv", ".tsv": "csv", ".json": "json", ".txt": "txt",
}
_CONTENT_TYPE = {
    "md": "text/markdown", "csv": "text/csv", "json": "application/json",
    "txt": "text/plain", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _kind_of(name: str) -> str:
    _, ext = os.path.splitext(name.lower())
    return _KIND_BY_EXT.get(ext, "txt")


async def save_file(
    conversation_id: uuid.UUID, path: str, content: str, agent_id: str | None,
    message_id: uuid.UUID | None = None,
) -> WorkspaceFile:
    # Preserve full relative path for folder support (e.g., "src/main.py") but
    # confine it: an agent must not write "../" into another conversation's
    # MinIO prefix or the DB under a traversal name.
    name = safe_relative_path(path)
    kind = _kind_of(name)

    # Guard: never overwrite existing content with empty/whitespace-only data
    if not content or not content.strip():
        async with async_session_maker() as db:
            res = await db.execute(
                select(WorkspaceFile).where(
                    WorkspaceFile.conversation_id == conversation_id,
                    WorkspaceFile.name == name,
                )
            )
            existing = res.scalar_one_or_none()
            if existing is not None:
                # File already exists and new content is empty — skip overwrite
                await db.refresh(existing)
                return existing
            # New file with empty content — still create it (agent may be scaffolding)
            content = ""

    data = content.encode("utf-8")
    size = len(data)

    storage_key: str | None = None
    inline: str | None = content
    if settings.storage_backend == "minio":
        storage_key = f"{conversation_id}/{name}"
        await asyncio.to_thread(
            object_storage.put, storage_key, data, _CONTENT_TYPE.get(kind, "text/plain")
        )
        inline = None  # offloaded

    async with async_session_maker() as db:
        res = await db.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.conversation_id == conversation_id,
                WorkspaceFile.name == name,
            )
        )
        f = res.scalar_one_or_none()
        if f is None:
            f = WorkspaceFile(
                conversation_id=conversation_id,
                message_id=message_id,
                name=name,
                kind=kind,
                content=inline,
                storage_key=storage_key,
                size_bytes=size,
                created_by_agent=agent_id,
                current_version=1,
            )
            db.add(f)
        else:
            # Save old version before overwriting — only if old content is non-empty
            # For MinIO storage, f.content is None — read from object storage
            old_content = f.content
            if old_content is None and f.storage_key:
                try:
                    old_content = await asyncio.to_thread(object_storage.get, f.storage_key)
                    if isinstance(old_content, bytes):
                        old_content = old_content.decode("utf-8", "ignore")
                except Exception:
                    old_content = None
            if old_content:
                ver = WorkspaceFileVersion(
                    file_id=f.id,
                    version_num=f.current_version,
                    content=old_content,
                    size_bytes=f.size_bytes,
                    author=agent_id,
                )
                db.add(ver)
                # Keep only the latest 10 versions
                old_versions = (
                    await db.execute(
                        select(WorkspaceFileVersion)
                        .where(WorkspaceFileVersion.file_id == f.id)
                        .order_by(WorkspaceFileVersion.version_num.desc())
                        .offset(9)  # Keep 10 (0-9), delete from 10th onwards
                    )
                ).scalars().all()
                for old in old_versions:
                    await db.delete(old)
            f.content = inline
            f.storage_key = storage_key
            f.size_bytes = size
            f.current_version += 1
        await db.commit()
        await db.refresh(f)
        return f


async def read_content(file: WorkspaceFile) -> str:
    """Return file text, fetching from MinIO when offloaded."""
    if file.content is not None:
        return file.content
    if file.storage_key:
        data = await asyncio.to_thread(object_storage.get, file.storage_key)
        return data.decode("utf-8", "ignore")
    return ""


async def get_existing_content(conversation_id: uuid.UUID, path: str) -> str | None:
    """Return current content of an existing workspace file, or None."""
    name = safe_relative_path(path)
    async with async_session_maker() as db:
        res = await db.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.conversation_id == conversation_id,
                WorkspaceFile.name == name,
            )
        )
        f = res.scalar_one_or_none()
        if f is None:
            return None
        return await read_content(f)
