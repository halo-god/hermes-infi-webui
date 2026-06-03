"""File browser: aggregate file listing across conversations."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.conversation import Conversation, Message
from app.db.models.user import User
from app.db.models.workspace import WorkspaceFile
from app.deps import get_current_user, get_db

router = APIRouter()


class FileItem(BaseModel):
    id: str
    name: str
    conversation_id: str
    conversation_title: str
    size: int | None = None
    created_at: str
    source: str = "upload"  # "upload" or "ai"


@router.get("/files", response_model=list[FileItem])
async def list_all_files(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all files across the user's conversations (uploads + AI-generated)."""
    # Get user's conversation IDs
    convos = (
        await db.execute(
            select(Conversation).where(Conversation.owner_id == user.id)
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
