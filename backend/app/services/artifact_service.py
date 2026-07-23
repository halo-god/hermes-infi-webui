"""Artifact service: extract executable code blocks from AI responses
and provide execution capabilities (SQL query, Python sandbox).

Inspired by ai-agent-book's `erp-agent` Artifact pattern: LLM generates
the artifact (SQL/code), system executes it — LLM never touches raw data.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trace import Artifact


# Regex to extract fenced code blocks from markdown
CODE_BLOCK_RE = re.compile(
    r"```(\w+)?\s*\n(.*?)```",
    re.DOTALL,
)

# Map language tags to artifact types
LANG_MAP = {
    "sql": "sql",
    "python": "python",
    "py": "python",
    "javascript": "javascript",
    "js": "javascript",
    "json": "json",
    "bash": "shell",
    "sh": "shell",
    "shell": "shell",
    "go": "go",
    "rust": "rust",
    "java": "java",
}


def extract_artifacts(text: str) -> list[dict]:
    """Extract executable code blocks from an AI response.

    Returns list of {artifact_type, content} dicts.
    Only extracts blocks with recognized language tags.
    """
    artifacts = []
    for match in CODE_BLOCK_RE.finditer(text):
        lang = (match.group(1) or "").lower().strip()
        code = match.group(2).strip()
        if not code:
            continue
        artifact_type = LANG_MAP.get(lang)
        if artifact_type:
            artifacts.append({"artifact_type": artifact_type, "content": code})
    return artifacts


async def save_artifacts(
    db: AsyncSession, conversation_id: uuid.UUID, message_id: uuid.UUID, text: str,
) -> list[Artifact]:
    """Extract and save artifacts from an AI response.

    Called after _finalize to persist any code blocks found in the response.
    """
    extracted = extract_artifacts(text)
    if not extracted:
        return []
    saved = []
    for item in extracted:
        artifact = Artifact(
            conversation_id=conversation_id,
            message_id=message_id,
            artifact_type=item["artifact_type"],
            content=item["content"],
            status="draft",
        )
        db.add(artifact)
        saved.append(artifact)
    if saved:
        await db.commit()
        for a in saved:
            await db.refresh(a)
    return saved


async def list_artifacts(
    db: AsyncSession, conversation_id: uuid.UUID,
) -> list[Artifact]:
    """List all artifacts for a conversation."""
    rows = (
        await db.execute(
            select(Artifact)
            .where(Artifact.conversation_id == conversation_id)
            .order_by(Artifact.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def update_artifact_result(
    db: AsyncSession, artifact_id: uuid.UUID, status: str, result: str,
) -> Artifact | None:
    """Update an artifact's execution result."""
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        return None
    artifact.status = status
    artifact.result = result[:10000]  # cap result size
    await db.commit()
    await db.refresh(artifact)
    return artifact
