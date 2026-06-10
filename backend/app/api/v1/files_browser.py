"""File browser: aggregate file listing across conversations."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, File as FastApiFile, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Conversation, Message
from app.db.models.user import User
from app.db.models.workspace import WorkspaceFile
from app.deps import get_current_user, get_db
from app.core import object_storage
from app.core.files import read_upload_capped
from app.config import settings

router = APIRouter()


class FileItem(BaseModel):
    id: str
    name: str
    conversation_id: str | None = None
    conversation_title: str | None = None
    size: int | None = None
    created_at: str
    source: str = "upload"  # "upload" or "ai"
    kind: str | None = None
    storage_key: str | None = None
    folder_path: str = "/"
    is_folder: bool = False


@router.get("/files", response_model=list[FileItem])
async def list_all_files(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all files across the user's conversations (uploads + AI-generated)."""
    # Get user's conversation IDs
    convos = (
        await db.execute(
            select(Conversation).where(
                Conversation.owner_id == user.id,
                Conversation.title != "__file_storage__",
            )
        )
    ).scalars().all()

    convo_map = {c.id: c.title for c in convos}
    if not convo_map:
        return []

    files: list[FileItem] = []

    # 1. User uploaded files (from message content.files)
    msgs = (
        await db.execute(
            select(Message).where(
                Message.conversation_id.in_(convo_map.keys()),
                Message.role == "user",
            )
        )
    ).scalars().all()

    for msg in msgs:
        content = msg.content or {}
        file_list = content.get("files") or []
        for f in file_list:
            files.append(
                FileItem(
                    id=f.get("id", ""),
                    name=f.get("name", "unknown"),
                    conversation_id=str(msg.conversation_id),
                    conversation_title=convo_map.get(msg.conversation_id, ""),
                    size=f.get("size"),
                    created_at=msg.created_at.isoformat() if msg.created_at else "",
                    source="upload",
                )
            )

    # 2. AI-generated workspace files
    ws_files = (
        await db.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.conversation_id.in_(convo_map.keys())
            )
        )
    ).scalars().all()

    for wf in ws_files:
        files.append(
            FileItem(
                id=str(wf.id),
                name=wf.name,
                conversation_id=str(wf.conversation_id),
                conversation_title=convo_map.get(wf.conversation_id, ""),
                size=wf.size_bytes,
                created_at=wf.created_at.isoformat() if wf.created_at else "",
                source="ai",
            )
        )

    # Sort by created_at descending
    files.sort(key=lambda f: f.created_at or "", reverse=True)
    return files


@router.get("/files/standalone", response_model=list[FileItem])
async def list_standalone_files(
    folder: str = "/",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's standalone files and subfolders in a given folder."""
    storage_convo = (
        await db.execute(
            select(Conversation).where(
                Conversation.owner_id == user.id,
                Conversation.title == "__file_storage__",
            )
        )
    ).scalars().first()

    if not storage_convo:
        return []

    ws_files = (
        await db.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.conversation_id == storage_convo.id,
                WorkspaceFile.folder_path == folder,
                WorkspaceFile.is_folder == False,  # noqa: E712
            )
        )
    ).scalars().all()

    # Real folder records in this folder
    db_folders = (
        await db.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.conversation_id == storage_convo.id,
                WorkspaceFile.folder_path == folder,
                WorkspaceFile.is_folder == True,  # noqa: E712
            )
        )
    ).scalars().all()

    # Also collect virtual subfolders: unique first path segment of files in deeper folders
    all_files = (
        await db.execute(
            select(WorkspaceFile.folder_path).where(
                WorkspaceFile.conversation_id == storage_convo.id,
                WorkspaceFile.folder_path.startswith(folder) if folder != "/" else WorkspaceFile.folder_path != "/",
                WorkspaceFile.folder_path != folder,
            )
        )
    ).all()

    subfolders: set[str] = set()
    prefix = folder.rstrip("/")
    for (fp,) in all_files:
        if not fp or fp == folder:
            continue
        # Get immediate child folder
        relative = fp[len(prefix):].lstrip("/")
        if "/" in relative:
            subfolders.add(prefix + "/" + relative.split("/")[0])
        else:
            subfolders.add(fp)

    result: list[FileItem] = []
    # Real folders from DB
    for f in db_folders:
        result.append(FileItem(
            id=str(f.id),
            name=f.name,
            created_at=f.created_at.isoformat() if f.created_at else "",
            source="folder",
            folder_path=f.folder_path or "/",
            is_folder=True,
        ))
    # Virtual subfolders (from file paths)
    known_paths = {f.folder_path for f in db_folders}
    for sf in sorted(subfolders):
        if sf in known_paths:
            continue
        folder_name = sf.rsplit("/", 1)[-1] if "/" in sf else sf
        result.append(FileItem(
            id=f"folder:{sf}",
            name=folder_name,
            created_at="",
            source="folder",
            folder_path=sf,
            is_folder=True,
        ))

    for wf in ws_files:
        result.append(FileItem(
            id=str(wf.id),
            name=wf.name,
            size=wf.size_bytes,
            created_at=wf.created_at.isoformat() if wf.created_at else "",
            source="upload",
            kind=wf.kind,
            storage_key=wf.storage_key,
            folder_path=wf.folder_path or "/",
        ))

    return result


@router.post("/files/folder", status_code=201)
async def create_folder(
    name: str,
    parent: str = "/",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a folder as a real DB record."""
    import re as _re
    clean_name = _re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name).strip("_. ") or "folder"
    folder_path = parent.rstrip("/") + "/" + clean_name

    # Ensure standalone conversation exists
    storage_convo = (
        await db.execute(
            select(Conversation).where(
                Conversation.owner_id == user.id,
                Conversation.title == "__file_storage__",
            )
        )
    ).scalars().first()
    if not storage_convo:
        storage_convo = Conversation(
            owner_id=user.id,
            title="__file_storage__",
            system_prompt="",
        )
        db.add(storage_convo)
        await db.flush()

    # Check if folder already exists
    existing = (
        await db.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.conversation_id == storage_convo.id,
                WorkspaceFile.name == clean_name,
                WorkspaceFile.folder_path == parent,
                WorkspaceFile.is_folder == True,  # noqa: E712
            )
        )
    ).scalars().first()
    if existing:
        raise HTTPException(409, "Folder already exists")

    wf = WorkspaceFile(
        conversation_id=storage_convo.id,
        name=clean_name,
        kind="folder",
        folder_path=parent,
        content="",
        size_bytes=0,
        is_folder=True,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)

    return {
        "id": str(wf.id),
        "name": clean_name,
        "kind": "folder",
        "folder_path": parent,
        "is_folder": True,
        "created_at": wf.created_at.isoformat() if wf.created_at else None,
    }


@router.post("/files/upload", response_model=FileItem, status_code=201)
async def upload_standalone_file(
    file: UploadFile = FastApiFile(...),
    folder: str = "/",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file without requiring a conversation. Files are stored in user's personal space."""
    import re as _re
    name = _re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", file.filename or "upload").strip("_. ") or "upload"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "bin"
    TEXT_EXTS = {"md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
                 "yaml", "yml", "toml", "sh", "bash", "log", "xml", "css", "diff", "patch"}

    raw = await read_upload_capped(file, settings.max_upload_bytes)
    content: str | None = None
    storage_key: str | None = None

    # Store to MinIO if available, else inline
    if settings.storage_backend == "minio":
        storage_key = f"standalone/{user.id}/{uuid.uuid4().hex}/{name}"
        await asyncio.to_thread(object_storage.put, storage_key, raw, file.content_type or "application/octet-stream")
    else:
        if ext in TEXT_EXTS:
            content = raw.decode("utf-8", "ignore")
        else:
            import base64
            content = base64.b64encode(raw).decode("ascii")

    # Create a "virtual" conversation for standalone files if not exists
    # Actually, let's use a special conversation_id = NULL approach
    # But WorkspaceFile requires conversation_id, so we need to handle this differently
    # Let's create a dedicated "File Storage" conversation per user
    from sqlalchemy import and_
    storage_convo = (
        await db.execute(
            select(Conversation).where(
                and_(
                    Conversation.owner_id == user.id,
                    Conversation.title == "__file_storage__",
                )
            )
        )
    ).scalars().first()

    if not storage_convo:
        storage_convo = Conversation(
            owner_id=user.id,
            title="__file_storage__",
            primary_agent_id="hermes",
        )
        db.add(storage_convo)
        await db.flush()

    wf = WorkspaceFile(
        conversation_id=storage_convo.id,
        name=name,
        folder_path=folder,
        kind=ext,
        content=content,
        storage_key=storage_key,
        size_bytes=len(raw),
        created_by_agent=None,
    )
    db.add(wf)
    await db.commit()
    await db.refresh(wf)

    return FileItem(
        id=str(wf.id),
        name=wf.name,
        size=wf.size_bytes,
        created_at=wf.created_at.isoformat() if wf.created_at else "",
        source="upload",
        kind=wf.kind,
        storage_key=wf.storage_key,
        folder_path=wf.folder_path or "/",
    )


@router.get("/files/{file_id}/content")
async def get_file_content(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get content of a standalone file by ID."""
    wf = (await db.execute(select(WorkspaceFile).where(WorkspaceFile.id == file_id))).scalars().first()
    if not wf:
        raise HTTPException(404, "File not found")

    # Verify ownership through conversation
    convo = (await db.execute(select(Conversation).where(Conversation.id == wf.conversation_id))).scalars().first()
    if not convo or convo.owner_id != user.id:
        raise HTTPException(403, "Not authorized")

    content = None
    if wf.content:
        content = wf.content
    elif wf.storage_key:
        try:
            raw = await asyncio.to_thread(object_storage.get, wf.storage_key)
            content = raw.decode("utf-8", "ignore")
        except Exception:
            content = None

    return {"id": str(wf.id), "name": wf.name, "kind": wf.kind, "content": content, "size": wf.size_bytes}


@router.delete("/files/{file_id}")
async def delete_standalone_file(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a standalone file."""
    wf = (await db.execute(select(WorkspaceFile).where(WorkspaceFile.id == file_id))).scalars().first()
    if not wf:
        raise HTTPException(404, "File not found")

    # Verify ownership through conversation
    convo = (await db.execute(select(Conversation).where(Conversation.id == wf.conversation_id))).scalars().first()
    if not convo or convo.owner_id != user.id:
        raise HTTPException(403, "Not authorized")

    # Delete from MinIO if stored there
    if wf.storage_key:
        try:
            await asyncio.to_thread(object_storage.delete, wf.storage_key)
        except Exception:
            pass

    await db.delete(wf)
    await db.commit()
    return {"status": "ok"}
