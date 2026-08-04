"""Bidirectional sync between DB AgentSkill and hermes-agent's filesystem skills.

Direction A (DB → FS): when an AgentSkill is created/updated/deleted/approved,
write/remove a SKILL.md in {HERMES_HOME}/skills/{slug}/ so hermes can use it.

Direction B (FS → DB): scan hermes skills/ directory for agent-created skills
and ingest them as AgentSkill rows.

DB is the source of truth (permissions, scope, audit, GEPA optimization).
hermes filesystem is a runtime projection.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _slugify(name: str) -> str:
    """Convert a skill name to agentskills.io-compliant slug."""
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "unnamed-skill"


def _get_hermes_home() -> str | None:
    """Get HERMES_HOME from settings, expanding ~."""
    raw = getattr(settings, "hermes_home", None) or os.path.expanduser("~/.hermes")
    p = os.path.expanduser(raw)
    return p if os.path.isdir(p) else None


def _render_skill_md(name: str, description: str, content: str, tags: list[str] | None = None) -> str:
    """Render an agentskills.io-standard SKILL.md from AgentSkill fields."""
    meta: dict = {"name": _slugify(name), "description": description[:1024]}
    if tags:
        meta["metadata"] = {"tags": tags}
    frontmatter = yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{frontmatter}---\n\n{content}\n"


# ── Direction A: DB AgentSkill → hermes filesystem ──

async def sync_skill_to_hermes(skill_id, name: str, description: str, content: str,
                                enabled: bool, trigger_conditions: dict | None = None) -> None:
    """Write (or remove) a SKILL.md for this skill in hermes's skills/ dir."""
    if not settings.hermes_skills_sync_enabled:
        return
    hermes_home = _get_hermes_home()
    if not hermes_home:
        return

    slug = _slugify(name)
    skill_dir = os.path.join(hermes_home, "skills", slug)

    if not enabled:
        # Remove from hermes if it exists (disabled = not projected).
        await _remove_skill_dir(skill_dir)
        return

    tags = []
    if trigger_conditions and isinstance(trigger_conditions, dict):
        tags = trigger_conditions.get("keywords") or []

    md = _render_skill_md(name, description, content, tags)

    try:
        os.makedirs(skill_dir, exist_ok=True)
        skill_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_path, "w", encoding="utf-8") as f:
            f.write(md)
        logger.info("Synced skill '%s' → %s", name, skill_path)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to sync skill '%s' to hermes FS", name, exc_info=True)


async def remove_skill_from_hermes(name: str) -> None:
    """Remove a skill from hermes's filesystem when deleted from DB."""
    if not settings.hermes_skills_sync_enabled:
        return
    hermes_home = _get_hermes_home()
    if not hermes_home:
        return
    slug = _slugify(name)
    await _remove_skill_dir(os.path.join(hermes_home, "skills", slug))


async def _remove_skill_dir(skill_dir: str) -> None:
    """Best-effort removal of a skill directory."""
    try:
        import shutil
        if os.path.isdir(skill_dir):
            shutil.rmtree(skill_dir)
            logger.info("Removed skill dir %s", skill_dir)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to remove skill dir %s", skill_dir, exc_info=True)


# ── Direction B: hermes filesystem → DB ──

@dataclass
class HermesSkillInfo:
    """Parsed SKILL.md from the filesystem."""
    slug: str
    name: str
    description: str
    content: str
    tags: list[str]


def list_hermes_fs_skills(hermes_home: str | None = None) -> list[HermesSkillInfo]:
    """Walk {HERMES_HOME}/skills/ and parse all SKILL.md files."""
    hermes_home = hermes_home or _get_hermes_home()
    if not hermes_home:
        return []
    skills_dir = os.path.join(hermes_home, "skills")
    if not os.path.isdir(skills_dir):
        return []

    results: list[HermesSkillInfo] = []
    for entry in os.listdir(skills_dir):
        skill_path = os.path.join(skills_dir, entry, "SKILL.md")
        if not os.path.isfile(skill_path):
            continue
        try:
            parsed = _parse_skill_md(Path(skill_path).read_text(encoding="utf-8"))
            if parsed:
                results.append(HermesSkillInfo(
                    slug=entry,
                    name=parsed["name"],
                    description=parsed["description"],
                    content=parsed["content"],
                    tags=parsed.get("tags", []),
                ))
        except Exception:  # noqa: BLE001
            logger.debug("Failed to parse %s", skill_path, exc_info=True)
    return results


def _parse_skill_md(raw: str) -> dict | None:
    """Parse a SKILL.md into {name, description, content, tags}."""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        # No frontmatter — treat whole file as content.
        return {"name": "unnamed", "description": "", "content": raw.strip(), "tags": []}
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    name = meta.get("name", "unnamed")
    description = meta.get("description", "")
    content = m.group(2).strip()
    tags = []
    nested_meta = meta.get("metadata") or {}
    if isinstance(nested_meta, dict):
        # agentskills.io layout: metadata.tags, or metadata.hermes.tags
        # (the hermes CLI's own convention, e.g. docker-manager's SKILL.md).
        t = nested_meta.get("tags")
        if t is None:
            hermes_meta = nested_meta.get("hermes")
            if isinstance(hermes_meta, dict):
                t = hermes_meta.get("tags")
        if isinstance(t, list):
            tags = t
        elif isinstance(t, str):
            tags = [t]
    return {"name": name, "description": description, "content": content, "tags": tags}


async def ingest_hermes_skills(db: AsyncSession, owner_id) -> dict:
    """Scan hermes FS skills and sync into DB. Returns {new, updated, skipped}."""
    from app.db.models.memory import AgentSkill
    from sqlalchemy import select

    fs_skills = list_hermes_fs_skills()
    if not fs_skills:
        return {"new": 0, "updated": 0, "skipped": 0}

    # Get existing DB skills by slug-matched name.
    existing = (await db.execute(
        select(AgentSkill).where(AgentSkill.owner_id == owner_id)
    )).scalars().all()
    existing_slugs = {_slugify(s.name): s for s in existing}

    new_count = 0
    updated_count = 0

    for fs_skill in fs_skills:
        existing_skill = existing_slugs.get(fs_skill.slug)
        if existing_skill is None:
            # New skill from hermes FS.
            db.add(AgentSkill(
                owner_id=owner_id,
                name=fs_skill.name,
                description=fs_skill.description,
                content=fs_skill.content,
                trigger_conditions={"keywords": fs_skill.tags} if fs_skill.tags else {},
                enabled=True,
            ))
            new_count += 1
        elif existing_skill.content != fs_skill.content:
            # Content changed in hermes FS — update DB.
            existing_skill.content = fs_skill.content
            existing_skill.description = fs_skill.description
            if fs_skill.tags:
                existing_skill.trigger_conditions = {"keywords": fs_skill.tags}
            updated_count += 1

    if new_count or updated_count:
        await db.commit()

    return {"new": new_count, "updated": updated_count, "skipped": len(fs_skills) - new_count - updated_count}


# Public alias for ZIP import / external parsers
parse_skill_md = _parse_skill_md
