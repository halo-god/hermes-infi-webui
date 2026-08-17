"""Bidirectional sync between DB AgentSkill and hermes-agent's filesystem skills.

Direction A (DB → FS): when an AgentSkill is created/updated/deleted/approved,
write/remove a SKILL.md in {HERMES_HOME}/skills/{slug}/ so hermes can use it.

Direction B (FS → DB): scan hermes skills/ directory for agent-created skills
and ingest them as AgentSkill rows.

DB is the source of truth (permissions, scope, audit, GEPA optimization).
hermes filesystem is a runtime projection.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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


# Frontmatter key carrying the DB skill UUID. Written on every platform sync
# so rename/delete can locate the exact directory (slug is NOT unique: CJK
# names degrade to "unnamed-skill", "API 测试"/"API 开发" both slug to "api").
_PLATFORM_ID_KEY = "platform_skill_id"


def _read_skill_dir_meta(skill_dir: str) -> dict:
    """Read {skill_dir}/SKILL.md frontmatter → {id, name} (empty when absent)."""
    skill_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_path):
        return {}
    try:
        raw = Path(skill_path).read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            return {}
        meta = yaml.safe_load(m.group(1)) or {}
        nested = meta.get("metadata") or {}
        nested = nested if isinstance(nested, dict) else {}
        return {
            "id": nested.get(_PLATFORM_ID_KEY),
            "name": meta.get("name"),
        }
    except Exception:  # noqa: BLE001
        return {}


def _resolve_skill_dir(home: str, slug: str, skill_id: str, name: str) -> str:
    """Resolve the skills/ dir for one skill, guarding against slug collisions.

    - No existing dir → use the plain slug dir.
    - Dir already carries THIS skill's platform id → reuse it (update).
    - Dir is a legacy platform write (no id marker, same name) → reuse and
      repair in place by overwriting with the id marker added.
    - Otherwise the slug is taken by a DIFFERENT skill → fall back to a
      `{slug}-{id8}` dir so CJK/similar names never overwrite each other.
    """
    base_dir = os.path.join(home, "skills", slug)
    if not os.path.isdir(base_dir):
        return base_dir
    meta = _read_skill_dir_meta(base_dir)
    if meta.get("id") == skill_id:
        return base_dir
    if meta.get("id") is None and meta.get("name") == name:
        return base_dir  # legacy platform write — repair in place
    return os.path.join(home, "skills", f"{slug}-{skill_id[:8]}")


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


def _hash_content(content: str) -> str:
    """sha256 of skill content — the sync loop's change detector."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _render_skill_md(name: str, description: str, content: str, tags: list[str] | None = None,
                     skill_id: str | None = None, content_hash: str | None = None) -> str:
    """Render an agentskills.io-standard SKILL.md from AgentSkill fields.

    frontmatter `name` keeps the ORIGINAL name (hermes displays it via
    frontmatter, the slug is only the directory name); the DB uuid travels in
    metadata.platform_skill_id for rename/delete lookup; metadata.content_hash
    lets Direction B recognise this file as a projection of the current DB
    version (loop breaker — see 0092).
    """
    meta: dict = {"name": name, "description": description[:1024]}
    if tags:
        meta["metadata"] = {"tags": tags}
    if skill_id:
        meta.setdefault("metadata", {})[_PLATFORM_ID_KEY] = skill_id
    if content_hash:
        meta.setdefault("metadata", {})["content_hash"] = content_hash
    frontmatter = yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{frontmatter}---\n\n{content}\n"


# ── Direction A: DB AgentSkill → hermes filesystem ──

def _remove_skill_dir_by_id(home: str, skill_id: str) -> None:
    """Remove every skills/ dir in `home` carrying this platform skill id."""
    skills_dir = os.path.join(home, "skills")
    if not os.path.isdir(skills_dir):
        return
    for entry in os.listdir(skills_dir):
        meta = _read_skill_dir_meta(os.path.join(skills_dir, entry))
        if meta.get("id") == skill_id:
            _remove_skill_dir(os.path.join(skills_dir, entry))


def _remove_skill_dir_by_name(home: str, name: str) -> None:
    """Remove legacy dirs (no platform id) belonging to THIS skill.

    Slug-based matching is deliberately avoided: CJK / similar names degrade
    to the same slug, so `entry == slug` would delete an UNRELATED skill's
    dir in a collision (its contents are then gone for good). The frontmatter
    `name` (original text, written since the id-marker era) is precise; a
    name-less legacy dir only matches via slug as a last resort.
    """
    skills_dir = os.path.join(home, "skills")
    if not os.path.isdir(skills_dir):
        return
    slug = _slugify(name)
    for entry in os.listdir(skills_dir):
        meta = _read_skill_dir_meta(os.path.join(skills_dir, entry))
        if meta.get("id") is not None:
            continue  # id-marked dirs are handled by _remove_skill_dir_by_id
        if meta.get("name") == name:
            _remove_skill_dir(os.path.join(skills_dir, entry))
        elif meta.get("name") in (None, "") and entry == slug:
            _remove_skill_dir(os.path.join(skills_dir, entry))


def _write_skill_to_home(home: str, slug: str, md: str, *, enabled: bool, name: str,
                         skill_id: str | None = None) -> None:
    """Write (or remove) one skill in one HERMES_HOME's skills/ dir."""
    if not enabled:
        # Disabled: remove this skill's dir(s) by id marker first, then by
        # legacy name/slug so renamed skills are not left behind.
        if skill_id:
            _remove_skill_dir_by_id(home, skill_id)
        _remove_skill_dir_by_name(home, name)
        return
    try:
        skill_dir = _resolve_skill_dir(home, slug, skill_id, name) if skill_id else (
            os.path.join(home, "skills", slug)
        )
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

    Loop breaker: the frontmatter carries content_hash, and the DB row's
    content_hash is refreshed AFTER the writes — Direction B then sees the
    file as a projection of the DB and skips it, so platform/evolution edits
    can never be bounced back by the next scan.
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

    sid = str(skill_id)
    slug = _slugify(name)
    tags = []
    if trigger_conditions and isinstance(trigger_conditions, dict):
        tags = trigger_conditions.get("keywords") or []
    # Hash is computed over the PARSED form (content.strip()) — Direction B
    # strips the file body when parsing, so a projection whose DB content has
    # trailing whitespace must still hash-match and stay a projection.
    content_hash = _hash_content(content.strip())
    md = _render_skill_md(name, description, content, tags, skill_id=sid, content_hash=content_hash)

    for home in homes:
        await asyncio.to_thread(
            _write_skill_to_home, home, slug, md,
            enabled=enabled, name=name, skill_id=sid,
        )

    # Record the hash the FS now mirrors so Direction B skips this row.
    # Warning (not debug): a silent failure here silently disables the
    # A→B→A loop breaker while the sync still reports success.
    try:
        from app.db.base import async_session_maker
        from app.db.models.memory import AgentSkill
        async with async_session_maker() as db:
            row = await db.get(AgentSkill, uuid.UUID(sid))
            if row is not None:
                row.content_hash = content_hash
                row.last_synced_at = datetime.now(timezone.utc)
                await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to record content_hash for skill %s", sid, exc_info=True)


async def remove_skill_from_hermes(name: str, skill_id=None) -> None:
    """Remove a skill from every home (global + all profile homes).

    `skill_id` is the authoritative key (id-marked dirs, incl. hash-suffixed
    collision dirs and renamed leftovers); `name` cleans up legacy dirs that
    predate the id marker.
    """
    if not settings.hermes_skills_sync_enabled:
        return
    homes = ([_get_hermes_home()] if _get_hermes_home() else []) + _profile_homes()
    for home in homes:
        if not home:
            continue
        if skill_id:
            _remove_skill_dir_by_id(home, str(skill_id))
        _remove_skill_dir_by_name(home, name)


def _remove_skill_dir(skill_dir: str) -> None:
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
    # DB uuid marker written by the platform (see _PLATFORM_ID_KEY).
    platform_skill_id: str | None = None
    # Hash of `content` recorded in the frontmatter by Direction A — when it
    # equals the DB row's content_hash, the file is a projection: skip.
    content_hash: str | None = None
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
    seen_slugs: dict[str, str] = {}
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
                    platform_skill_id=parsed.get("platform_skill_id"),
                    content_hash=parsed.get("content_hash"),
                ))
                # Slug-collision health check (1c): two dirs whose slugs slugify
                # to the same key (e.g. CJK names → "unnamed-skill") would have
                # overwritten each other before the id-marker fix — surface it.
                key = _slugify(parsed["name"])
                if key in seen_slugs and seen_slugs[key] != entry:
                    logger.warning(
                        "Skill slug collision in %s: %r and %r both resolve to '%s' — "
                        "re-sync via platform to disambiguate",
                        skills_dir, seen_slugs[key], entry, key,
                    )
                seen_slugs.setdefault(key, entry)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to parse %s", skill_path, exc_info=True)
    return results


def _parse_skill_md(raw: str) -> dict | None:
    """Parse a SKILL.md into {name, description, content, tags, content_hash}."""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        # No frontmatter — treat whole file as content.
        return {"name": "unnamed", "description": "", "content": raw.strip(), "tags": [],
                "platform_skill_id": None, "content_hash": None}
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
    content_hash = nested_meta.get("content_hash") if isinstance(nested_meta, dict) else None
    if content_hash is not None and not isinstance(content_hash, str):
        content_hash = None
    return {
        "name": name, "description": description, "content": content, "tags": tags,
        "platform_skill_id": nested_meta.get(_PLATFORM_ID_KEY) if isinstance(nested_meta, dict) else None,
        "content_hash": content_hash,
    }


async def ingest_hermes_skills(db: AsyncSession, owner_id) -> dict:
    """Scan hermes FS skills (global + every profile home) into DB.

    Returns {new, updated, skipped}. Skills under a profile home are ingested
    bound to that profile (AgentSkill.profile_id) — this is what closes the
    multi-profile loop: per-profile agent-created skills used to be invisible
    to the DB, and DB-evolved skills never reached that profile's home.

    Matching prefers the platform id marker (frontmatter metadata.platform_
    skill_id) over slug — slugs collide for CJK/similar names, the id marker
    is unique. Agent-created rows (origin='agent') whose FS dir disappeared
    are tombstoned (enabled=False) so deletions on the hermes side are
    reflected; platform rows are never touched by the scan.
    """
    from app.db.models.agent import Profile
    from app.db.models.memory import AgentSkill
    from sqlalchemy import select

    # Existing DB skills (owner + profile scoped).
    existing = (await db.execute(
        select(AgentSkill).where(
            (AgentSkill.owner_id == owner_id) | (AgentSkill.profile_id.is_not(None))
        )
    )).scalars().all()
    existing_by_id: dict[str, AgentSkill] = {}
    existing_by_slug: dict[str, AgentSkill] = {}
    for s in existing:
        existing_by_id.setdefault(str(s.id), s)
        existing_by_slug.setdefault(_slugify(s.name), s)

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
    matched_ids: set[str] = set()  # DB rows this scan found on the FS

    def _ingest(fs_skill: HermesSkillInfo, profile_id: uuid.UUID | None) -> None:
        nonlocal new_count, updated_count, total
        total += 1
        by_id_hit = (
            existing_by_id.get(fs_skill.platform_skill_id)
            if fs_skill.platform_skill_id else None
        )
        if by_id_hit is not None and by_id_hit.profile_id != profile_id:
            # The file carries the platform_skill_id of a row in ANOTHER
            # scope — it is a Direction-A projection (DB→FS) of e.g. a global
            # skill into a profile home. Never re-ingest it: a duplicate row
            # created here gets a fresh id the projection file doesn't know,
            # so the next scan can't match it by id — it tombstones and yet
            # another duplicate is created, growing the table without bound.
            return
        existing_skill = by_id_hit or existing_by_slug.get(_slugify(fs_skill.name))
        if existing_skill is not None and existing_skill.profile_id != profile_id:
            # Slug matched a row in ANOTHER scope (global vs profile, or two
            # profiles): adopting it would silently merge distinct skills
            # (and never tombstone either). Treat as unmatched — create the
            # correct profile-scoped row instead.
            existing_skill = None
        if existing_skill is None:
            fs_hash = _hash_content(fs_skill.content)
            db.add(AgentSkill(
                owner_id=owner_id,
                profile_id=profile_id,
                name=fs_skill.name,
                description=fs_skill.description,
                content=fs_skill.content,
                trigger_conditions={"keywords": fs_skill.tags} if fs_skill.tags else {},
                enabled=True,
                origin="agent",
                content_hash=fs_hash,
                last_synced_at=datetime.now(timezone.utc),
            ))
            new_count += 1
        else:
            matched_ids.add(str(existing_skill.id))
            fs_hash = _hash_content(fs_skill.content)
            # Loop breaker: when the row's content_hash equals the parsed file
            # hash, the file is a projection of the current DB version (or
            # simply unchanged) — never bounce the DB back. The hash is stored
            # over the parsed form (see sync_skill_to_hermes), so trailing-
            # whitespace normalisation can't cause a false "changed" either.
            if existing_skill.content_hash and existing_skill.content_hash == fs_hash:
                return
            if (
                existing_skill.profile_id == profile_id
                and existing_skill.origin == "agent"
            ):
                # Content changed on the hermes side (or first sync since
                # 0092, hash was NULL) — FS wins, but ONLY for agent-origin
                # rows. A hash mismatch on a platform row means the FS copy
                # drifted (failed write / manual edit); the DB stays the
                # source of truth for platform skills (AGENTS.md invariant) —
                # leave it alone and let Direction A re-project.
                existing_skill.content = fs_skill.content
                existing_skill.description = fs_skill.description
                existing_skill.content_hash = fs_hash
                existing_skill.last_synced_at = datetime.now(timezone.utc)
                if fs_skill.tags:
                    existing_skill.trigger_conditions = {"keywords": fs_skill.tags}
                updated_count += 1
            elif existing_skill.origin == "platform":
                logger.warning(
                    "Skill scan: FS copy of platform skill %r drifted "
                    "(hash mismatch) — keeping DB content",
                    existing_skill.name,
                )

    # 1. Global skills dir.
    for fs_skill in await asyncio.to_thread(list_hermes_fs_skills):
        _ingest(fs_skill, profile_id=None)

    # 2. Every profile home — skills there belong to that profile.
    for home, pid in home_to_profile.items():
        for fs_skill in await asyncio.to_thread(list_hermes_fs_skills, home):
            _ingest(fs_skill, profile_id=pid)

    # 3. Tombstone: agent-created rows (origin='agent') whose FS dir vanished
    #    are disabled so deleted hermes skills stop being injected. Platform
    #    rows are never touched — DB is the source of truth for them.
    tombstoned = 0
    for s in existing:
        if s.enabled and s.origin == "agent" and str(s.id) not in matched_ids:
            s.enabled = False
            tombstoned += 1

    if new_count or updated_count or tombstoned:
        await db.commit()

    return {
        "new": new_count, "updated": updated_count,
        "skipped": total - new_count - updated_count,
        "tombstoned": tombstoned,
    }


# Public alias for ZIP import / external parsers
parse_skill_md = _parse_skill_md
