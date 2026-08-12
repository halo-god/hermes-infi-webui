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
import uuid
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


def _profile_homes() -> list[str]:
    """All per-profile HERMES_HOME dirs under {hermes_home}/profiles/*.

    Each profile runs its agent with HERMES_HOME=<home>/profiles/<handle>,
    so a profile's agent only reads skills from ITS OWN home — global skills
    must be projected into every profile home to be usable there.
    """
    base = _get_hermes_home()
    if not base:
        return []
    profiles_dir = os.path.join(base, "profiles")
    if not os.path.isdir(profiles_dir):
        return []
    homes = []
    for entry in os.listdir(profiles_dir):
        home = os.path.join(profiles_dir, entry)
        if os.path.isdir(home):
            homes.append(home)
    return homes


async def _skill_profile_id(skill_id) -> uuid.UUID | None:
    """Resolve the skill's profile binding (AgentSkill.profile_id)."""
    from app.db.base import async_session_maker
    from app.db.models.memory import AgentSkill
    try:
        async with async_session_maker() as db:
            row = await db.get(AgentSkill, uuid.UUID(str(skill_id)))
            return row.profile_id if row else None
    except Exception:  # noqa: BLE001 — best-effort, fall back to global
        logger.debug("skill profile resolution failed", exc_info=True)
        return None


async def _profile_home(profile_id: uuid.UUID) -> str | None:
    """HERMES_HOME of a specific profile (dirname of its config path)."""
    from app.db.base import async_session_maker
    from app.db.models.agent import Profile
    try:
        async with async_session_maker() as db:
            p = await db.get(Profile, profile_id)
            if p is None or not p.path:
                return None
            home = os.path.dirname(os.path.expanduser(p.path))
            return home if os.path.isdir(home) else None
    except Exception:  # noqa: BLE001
        logger.debug("profile home resolution failed", exc_info=True)
        return None


def _render_skill_md(name: str, description: str, content: str, tags: list[str] | None = None) -> str:
    """Render an agentskills.io-standard SKILL.md from AgentSkill fields."""
    meta: dict = {"name": _slugify(name), "description": description[:1024]}
    if tags:
        meta["metadata"] = {"tags": tags}
    frontmatter = yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{frontmatter}---\n\n{content}\n"


# ── Direction A: DB AgentSkill → hermes filesystem ──

def _write_skill_to_home(home: str, slug: str, md: str, *, enabled: bool, name: str) -> None:
    """Write (or remove) one skill in one HERMES_HOME's skills/ dir."""
    skill_dir = os.path.join(home, "skills", slug)
    if not enabled:
        try:
            import shutil
            if os.path.isdir(skill_dir):
                shutil.rmtree(skill_dir)
                logger.info("Removed skill dir %s", skill_dir)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to remove skill dir %s", skill_dir, exc_info=True)
        return
    try:
        os.makedirs(skill_dir, exist_ok=True)
        skill_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_path, "w", encoding="utf-8") as f:
            f.write(md)
        logger.info("Synced skill '%s' → %s", name, skill_path)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to sync skill '%s' to hermes FS (%s)", name, skill_dir, exc_info=True)


async def sync_skill_to_hermes(skill_id, name: str, description: str, content: str,
                                enabled: bool, trigger_conditions: dict | None = None) -> None:
    """Write (or remove) a SKILL.md for this skill across the relevant homes.

    Multi-profile aware: a skill bound to a profile (AgentSkill.profile_id)
    only exists in THAT profile's HERMES_HOME (each profile's agent reads only
    its own home). Global / owner / team skills project to the global
    skills/ dir AND every profile home so all profiles can use them.
    """
    if not settings.hermes_skills_sync_enabled:
        return

    profile_id = await _skill_profile_id(skill_id)
    if profile_id is not None:
        home = await _profile_home(profile_id)
        if not home:
            return  # profile home not resolvable — nothing to project to
        homes = [home]
    else:
        global_home = _get_hermes_home()
        homes = ([global_home] if global_home else []) + _profile_homes()
        homes = [h for h in homes if h]
    if not homes:
        return

    slug = _slugify(name)
    tags = []
    if trigger_conditions and isinstance(trigger_conditions, dict):
        tags = trigger_conditions.get("keywords") or []
    md = _render_skill_md(name, description, content, tags)

    for home in homes:
        _write_skill_to_home(home, slug, md, enabled=enabled, name=name)


async def remove_skill_from_hermes(name: str) -> None:
    """Remove a skill from every home (global + all profile homes)."""
    if not settings.hermes_skills_sync_enabled:
        return
    homes = ([_get_hermes_home()] if _get_hermes_home() else []) + _profile_homes()
    slug = _slugify(name)
    for home in homes:
        if home:
            await _remove_skill_dir(os.path.join(home, "skills", slug))


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
    # Set when the skill was found under a profile's home (multi-profile scan).
    profile_id: uuid.UUID | None = None


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
    """Scan hermes FS skills (global + every profile home) into DB.

    Returns {new, updated, skipped}. Skills under a profile home are ingested
    bound to that profile (AgentSkill.profile_id) — this is what closes the
    multi-profile loop: per-profile agent-created skills used to be invisible
    to the DB, and DB-evolved skills never reached that profile's home.
    """
    from app.db.models.agent import Profile
    from app.db.models.memory import AgentSkill
    from sqlalchemy import select

    # Existing DB skills by slug-matched name (owner + profile scoped).
    existing = (await db.execute(
        select(AgentSkill).where(
            (AgentSkill.owner_id == owner_id) | (AgentSkill.profile_id.is_not(None))
        )
    )).scalars().all()
    existing_slugs = {_slugify(s.name): s for s in existing}

    # Map profile homes → profile ids (dirname of each profile's config path).
    profiles = (await db.execute(select(Profile))).scalars().all()
    home_to_profile: dict[str, uuid.UUID] = {}
    for p in profiles:
        if p.path:
            home = os.path.dirname(os.path.expanduser(p.path))
            if home and os.path.isdir(home):
                home_to_profile[home] = p.id

    new_count = 0
    updated_count = 0
    total = 0

    def _ingest(fs_skill: HermesSkillInfo, profile_id: uuid.UUID | None) -> None:
        nonlocal new_count, updated_count, total
        total += 1
        existing_skill = existing_slugs.get(fs_skill.slug)
        if existing_skill is None:
            db.add(AgentSkill(
                owner_id=owner_id,
                profile_id=profile_id,
                name=fs_skill.name,
                description=fs_skill.description,
                content=fs_skill.content,
                trigger_conditions={"keywords": fs_skill.tags} if fs_skill.tags else {},
                enabled=True,
            ))
            new_count += 1
        elif (existing_skill.profile_id == profile_id
                and existing_skill.content != fs_skill.content):
            # Content changed in hermes FS — update DB (same profile scope).
            existing_skill.content = fs_skill.content
            existing_skill.description = fs_skill.description
            if fs_skill.tags:
                existing_skill.trigger_conditions = {"keywords": fs_skill.tags}
            updated_count += 1

    # 1. Global skills dir.
    for fs_skill in list_hermes_fs_skills():
        _ingest(fs_skill, profile_id=None)

    # 2. Every profile home — skills there belong to that profile.
    for home, pid in home_to_profile.items():
        for fs_skill in list_hermes_fs_skills(home):
            _ingest(fs_skill, profile_id=pid)

    if new_count or updated_count:
        await db.commit()

    return {
        "new": new_count, "updated": updated_count,
        "skipped": total - new_count - updated_count,
    }


# Public alias for ZIP import / external parsers
parse_skill_md = _parse_skill_md
