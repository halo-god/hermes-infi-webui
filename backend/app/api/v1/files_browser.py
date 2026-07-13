"""File browser: aggregate file listing across conversations."""
from __future__ import annotations

import asyncio
import re
import uuid

import urllib.parse

from fastapi import APIRouter, Depends, File as FastApiFile, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Conversation, Message
from app.db.models.user import User
from app.db.models.workspace import WorkspaceFile
from app.deps import get_current_user, get_db, user_from_ticket_or_header
from app.core import object_storage
from app.core.files import read_upload_capped, extract_pdf_text, is_text_extractable, OFFICE_EXTRACTORS
from app.config import settings

router = APIRouter()

_TEXT_EXTS = frozenset({
    "txt", "md", "csv", "json", "html", "htm", "css", "js", "ts", "tsx",
    "py", "go", "rs", "yaml", "yml", "toml", "sh", "bash", "log", "xml",
    "diff", "patch",
})


async def _require_file_owner(
    db: AsyncSession, file_id: uuid.UUID, user: User
) -> WorkspaceFile:
    """Load a WorkspaceFile and verify the requesting user owns it."""
    wf = (
        await db.execute(select(WorkspaceFile).where(WorkspaceFile.id == file_id))
    ).scalars().first()
    if not wf:
        raise HTTPException(404, "File not found")
    convo = (
        await db.execute(select(Conversation).where(Conversation.id == wf.conversation_id))
    ).scalars().first()
    if not convo or convo.owner_id != user.id:
        raise HTTPException(403, "Not authorized")
    return wf


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
    limit: int = Query(200, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all files across the user's conversations (uploads + AI-generated).
    Bounded by limit to prevent OOM on users with many conversations."""
    # Get user's conversation IDs (bounded)
    convos = (
        await db.execute(
            select(Conversation).where(
                Conversation.owner_id == user.id,
                Conversation.title != "__file_storage__",
            ).limit(200)
        )
    ).scalars().all()

    convo_map = {c.id: c.title for c in convos}
    if not convo_map:
        return []

    files: list[FileItem] = []

    # 1. User uploaded files (from message content.files) - bounded
    msgs = (
        await db.execute(
            select(Message).where(
                Message.conversation_id.in_(convo_map.keys()),
                Message.role == "user",
            ).limit(limit)
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
    """List user's standalone files, AI-generated files, and subfolders in a given folder."""
    # AI-generated files: single JOIN query (was: load all convos then IN-clause)
    ai_rows = (
        await db.execute(
            select(WorkspaceFile, Conversation.title)
            .join(Conversation, WorkspaceFile.conversation_id == Conversation.id)
            .where(
                Conversation.owner_id == user.id,
                Conversation.title != "__file_storage__",
                WorkspaceFile.folder_path == folder,
                WorkspaceFile.is_folder == False,  # noqa: E712
            )
            .limit(200)
        )
    ).all()

    ai_files: list[FileItem] = []
    for wf, convo_title in ai_rows:
        ai_files.append(FileItem(
            id=str(wf.id),
            name=wf.name,
            conversation_id=str(wf.conversation_id),
            conversation_title=convo_title,
            size=wf.size_bytes,
            created_at=wf.created_at.isoformat() if wf.created_at else "",
            source="ai",
            kind=wf.kind,
            storage_key=wf.storage_key,
            folder_path=wf.folder_path or "/",
        ))

    # Standalone storage conversation
    storage_convo = (
        await db.execute(
            select(Conversation).where(
                Conversation.owner_id == user.id,
                Conversation.title == "__file_storage__",
            )
        )
    ).scalars().first()

    result: list[FileItem] = []

    if storage_convo:
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

        # Also collect virtual subfolders: unique first path segment of files in deeper folders.
        # Only real FILES participate — folder rows store their parent in folder_path (a different
        # semantic) and would otherwise fabricate phantom subfolders that duplicate real ones.
        all_files = (
            await db.execute(
                select(WorkspaceFile.folder_path).where(
                    WorkspaceFile.conversation_id == storage_convo.id,
                    WorkspaceFile.is_folder == False,  # noqa: E712
                    WorkspaceFile.folder_path.startswith(folder) if folder != "/" else WorkspaceFile.folder_path != "/",
                    WorkspaceFile.folder_path != folder,
                )
            )
        ).all()

        def _norm_prefix(p: str) -> str:
            """Normalized no-trailing-slash prefix; root → empty string."""
            return p.rstrip("/")

        subfolders: set[str] = set()
        prefix = _norm_prefix(folder)
        for (fp,) in all_files:
            if not fp or fp == folder:
                continue
            relative = fp[len(prefix):].lstrip("/")
            if "/" in relative:
                subfolders.add(prefix + "/" + relative.split("/")[0])
            else:
                subfolders.add(fp)

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
        # Virtual subfolders (from file paths only)
        # Build full path of each real folder for dedup: parent + name
        known_paths: set[str] = set()
        for f in db_folders:
            fp = _norm_prefix(f.folder_path or "/")
            full = f"/{f.name}" if not fp else f"{fp}/{f.name}"
            known_paths.add(full)
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

    # Merge AI files into result (they appear alongside standalone files in the folder)
    result.extend(ai_files)
    return result


@router.post("/files/folder", status_code=201)
async def create_folder(
    name: str,
    parent: str = "/",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a folder as a real DB record."""
    clean_name = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name).strip("_. ") or "folder"

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
            primary_agent_id="hermes",
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
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", file.filename or "upload").strip("_. ") or "upload"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "bin"

    raw = await read_upload_capped(file, settings.max_upload_bytes)
    OFFLOAD_THRESHOLD = 256 * 1024  # 256 KiB
    content: str | None = None
    storage_key: str | None = None

    # Large files or minio backend → offload binary; extract text when possible
    if len(raw) > OFFLOAD_THRESHOLD or settings.storage_backend == "minio":
        storage_key = f"standalone/{user.id}/{uuid.uuid4().hex}/{name}"
        await asyncio.to_thread(
            object_storage.put,
            storage_key,
            raw,
            file.content_type or "application/octet-stream",
        )
        if ext in OFFICE_EXTRACTORS:
            content = OFFICE_EXTRACTORS[ext](raw) or "<p><em>(无法解析文档内容)</em></p>"
        elif is_text_extractable(ext):
            if ext == "pdf":
                content = extract_pdf_text(raw)
            elif ext in {"md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
                       "yaml", "yml", "toml", "sh", "bash", "log", "xml", "css", "diff", "patch"}:
                content = raw.decode("utf-8", "ignore")
            else:
                content = None
    else:
        # Small files → inline
        if ext in OFFICE_EXTRACTORS:
            content = OFFICE_EXTRACTORS[ext](raw) or "<p><em>(无法解析文档内容)</em></p>"
        elif ext in {"md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
                   "yaml", "yml", "toml", "sh", "bash", "log", "xml", "css", "diff", "patch"}:
            content = raw.decode("utf-8", "ignore")
        elif ext == "pdf":
            content = extract_pdf_text(raw)
        else:
            import base64
            content = base64.b64encode(raw).decode("ascii")

    # Create a "virtual" conversation for standalone files if not exists
    # Actually, let's use a special conversation_id = NULL approach
    # But WorkspaceFile requires conversation_id, so we need to handle this differently
    # Let's create a dedicated "File Storage" conversation per user
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



@router.get("/files/{file_id}/raw")
async def get_file_raw(
    file_id: uuid.UUID,
    request: Request,
    ticket: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Download a standalone file by ID. Auth via media ticket (URL) or Bearer."""
    user = await user_from_ticket_or_header(ticket, request, db)
    wf = await _require_file_owner(db, file_id, user)

    content: bytes | None = None
    content_type = "application/octet-stream"
    if wf.storage_key:
        try:
            content = await asyncio.to_thread(object_storage.get, wf.storage_key)
        except Exception:
            raise HTTPException(404, "File not found in storage")
    elif wf.content:
        content = wf.content.encode("utf-8") if isinstance(wf.content, str) else wf.content
        if wf.kind in _TEXT_EXTS:
            content_type = "text/plain; charset=utf-8"
    else:
        raise HTTPException(404, "File has no content")

    filename = wf.name or "download"
    disposition = f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": disposition},
    )


@router.get("/files/folders")
async def list_all_folders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all folder paths for the user's standalone file storage."""
    storage_convo = (
        await db.execute(
            select(Conversation).where(
                Conversation.owner_id == user.id,
                Conversation.title == "__file_storage__",
            )
        )
    ).scalars().first()

    if not storage_convo:
        return [{"path": "/", "label": "根目录"}]

    # Real folder records
    db_folders = (
        await db.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.conversation_id == storage_convo.id,
                WorkspaceFile.is_folder == True,  # noqa: E712
            )
        )
    ).scalars().all()

    # Collect all unique folder_path values from non-folder files too
    all_paths = (
        await db.execute(
            select(WorkspaceFile.folder_path).where(
                WorkspaceFile.conversation_id == storage_convo.id,
            )
        )
    ).all()

    paths: set[str] = {"/"}
    for f in db_folders:
        fp = (f.folder_path.rstrip("/") + "/" + f.name) if f.folder_path else "/" + f.name
        paths.add(fp)
    for (fp,) in all_paths:
        if fp:
            paths.add(fp)

    # Build list with labels
    result = []
    for p in sorted(paths):
        label = "根目录" if p == "/" else p.rsplit("/", 1)[-1] or p
        result.append({"path": p, "label": label})
    return result


class MoveFileRequest(BaseModel):
    target_folder: str


@router.put("/files/{file_id}/move")
async def move_file_to_folder(
    file_id: uuid.UUID,
    body: MoveFileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Move a standalone file into a different folder."""
    wf = await _require_file_owner(db, file_id, user)

    if wf.is_folder:
        raise HTTPException(400, "Cannot move folders, only files")

    target = body.target_folder if body.target_folder else "/"
    if not target.startswith("/"):
        target = "/" + target

    old_folder = wf.folder_path or "/"
    wf.folder_path = target
    await db.commit()
    await db.refresh(wf)

    return {
        "status": "ok",
        "id": str(wf.id),
        "name": wf.name,
        "old_folder": old_folder,
        "new_folder": target,
    }


@router.get("/files/{file_id}/content")
async def get_file_content(
    file_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get content of a standalone file by ID."""
    wf = await _require_file_owner(db, file_id, user)

    content = None
    if wf.content:
        content = wf.content
    elif wf.storage_key:
        try:
            raw = await asyncio.to_thread(object_storage.get, wf.storage_key)
            if wf.kind in OFFICE_EXTRACTORS:
                # Re-extract HTML from the stored original bytes.
                content = OFFICE_EXTRACTORS[wf.kind](raw) or None
            elif is_text_extractable(wf.kind):
                content = raw.decode("utf-8", "ignore")
            else:
                content = None
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
    wf = await _require_file_owner(db, file_id, user)

    # Delete from MinIO if stored there
    if wf.storage_key:
        try:
            await asyncio.to_thread(object_storage.delete, wf.storage_key)
        except Exception:
            pass

    await db.delete(wf)
    await db.commit()
    return {"status": "ok"}


@router.delete("/files/folder/{folder_id}")
async def delete_folder(
    folder_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a folder and everything inside it (recursive).

    Removes the folder row, all descendant files (matching the folder's full
    path or any deeper path beneath it), and their MinIO objects. Descendant
    subfolders are removed too. Conversations are not affected.
    """
    folder = await _require_file_owner(db, folder_id, user)
    if not folder.is_folder:
        raise HTTPException(400, "目标不是文件夹")

    # Full path of this folder (folder_path stores the PARENT, so join name).
    fp_parent = (folder.folder_path or "/").rstrip("/")
    full_path = f"/{folder.name}" if not fp_parent else f"{fp_parent}/{folder.name}"

    # Everything beneath this folder: files/folders whose folder_path equals
    # the full path OR starts with "full_path/" (deeper nesting).
    prefix = full_path + "/"
    descendants = (
        await db.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.conversation_id == folder.conversation_id,
                or_(
                    WorkspaceFile.folder_path == full_path,
                    WorkspaceFile.folder_path.startswith(prefix),
                ),
            )
        )
    ).scalars().all()

    # Clean MinIO objects for any stored binaries (best-effort).
    for d in descendants:
        if d.storage_key:
            try:
                await asyncio.to_thread(object_storage.delete, d.storage_key)
            except Exception:
                pass

    if descendants:
        await db.execute(
            delete(WorkspaceFile).where(
                WorkspaceFile.id.in_([d.id for d in descendants])
            )
        )
    await db.delete(folder)
    await db.commit()
    return {"status": "ok", "deleted": len(descendants) + 1}
