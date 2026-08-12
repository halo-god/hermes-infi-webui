"""Sync hermes-agent advanced config to config.yaml files.

Writes the admin-configured settings into hermes's config.yaml so they persist
across restarts and survive agent self-edits (env vars would be overwritten).

Strategy:
- Write to the global ~/.hermes/config.yaml (base defaults for all profiles)
- Write to each active profile's config.yaml (~/.hermes/profiles/<name>/config.yaml)
- Uses YAML deep-merge to avoid clobbering existing keys
"""
from __future__ import annotations

import logging
import os

import yaml

from app.config import settings

logger = logging.getLogger(__name__)


def _get_hermes_home() -> str | None:
    raw = getattr(settings, "hermes_home", None) or os.path.expanduser("~/.hermes")
    p = os.path.expanduser(raw)
    return p if os.path.isdir(p) else None


def _build_config_overrides() -> dict:
    """Build the config dict from our settings to merge into hermes config.yaml.

    `reasoning_effort` must live under `agent:` — hermes only resolves it from
    agent.reasoning_effort (hermes_constants.resolve_reasoning_config); the
    top-level key the platform used to write is ignored by the agent.
    """
    return {
        "prompt_caching": {"cache_ttl": settings.hermes_prompt_cache_ttl},
        "terminal": {
            "backend": settings.hermes_terminal_backend,
            "persistent_shell": settings.hermes_persistent_shell,
        },
        "compression": {"enabled": settings.hermes_compression_enabled},
        "tool_output": {"max_bytes": settings.hermes_tool_output_max_bytes},
        "privacy": {"redact_pii": settings.hermes_redact_pii},
        "agent": {"reasoning_effort": settings.hermes_reasoning_effort},
    }


_LEGACY_TOP_LEVEL_KEYS = {
    # Written by older platform versions at the wrong level; hermes never
    # reads them there. Removed on next sync so they stop shadowing the
    # real agent.reasoning_effort (and confusing manual inspection).
    "reasoning_effort",
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override values win."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _sync_config_file(config_path: str, overrides: dict) -> bool:
    """Deep-merge overrides into a config.yaml file. Returns True if written."""
    if not os.path.isfile(config_path):
        return False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}
        if isinstance(existing, dict):
            # One-time cleanup of keys the platform used to write at a level
            # hermes ignores (see _LEGACY_TOP_LEVEL_KEYS).
            for key in _LEGACY_TOP_LEVEL_KEYS:
                existing.pop(key, None)
        merged = _deep_merge(existing, overrides)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(merged, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return True
    except Exception:
        logger.debug("Failed to sync config to %s", config_path, exc_info=True)
        return False


async def sync_hermes_configs() -> dict:
    """Write admin config to global + all profile config.yaml files.

    Called on Runner startup (after warmup reads DB overrides) and can be
    triggered manually. Returns a summary of what was synced.
    """
    hermes_home = _get_hermes_home()
    if not hermes_home:
        return {"synced": [], "skipped": "hermes home not found"}

    overrides = _build_config_overrides()
    synced: list[str] = []

    # 1. Global config (~/.hermes/config.yaml)
    global_path = os.path.join(hermes_home, "config.yaml")
    if _sync_config_file(global_path, overrides):
        synced.append("global")

    # 2. All profile configs (~/.hermes/profiles/*/config.yaml) — but only
    #    for profiles that still exist in the DB: deleted profiles leave
    #    their home dir behind, and writing overrides into a tombstone is
    #    pointless (and resurrects config for an assistant that is gone).
    from app.db.base import async_session_maker
    from app.db.models.agent import Profile
    from sqlalchemy import select

    profiles_dir = os.path.join(hermes_home, "profiles")
    if os.path.isdir(profiles_dir):
        try:
            async with async_session_maker() as db:
                rows = (await db.execute(select(Profile.handle))).scalars().all()
            # DB handles are `hermes-<name>`; the home dir is `<name>`.
            active_handles = {h.removeprefix("hermes-") for h in rows}
        except Exception:  # noqa: BLE001 — DB down: fall back to syncing all dirs
            logger.debug("Failed to load active profile handles", exc_info=True)
            active_handles = None
        for entry in os.listdir(profiles_dir):
            if active_handles is not None and entry not in active_handles:
                continue
            profile_path = os.path.join(profiles_dir, entry, "config.yaml")
            if _sync_config_file(profile_path, overrides):
                synced.append(f"profile:{entry}")

    if synced:
        logger.info("Synced hermes config to: %s", ", ".join(synced))
    return {"synced": synced}


async def sync_profile_soul(profile) -> None:
    """Project Profile.system_prompt into {profile_home}/SOUL.md.

    hermes reads SOUL.md from HERMES_HOME as its persistent persona file;
    the platform's DB system_prompt (also injected per-turn via【角色设定】)
    should not drift from it. Called whenever a profile's system_prompt
    changes — including prompt-evolution auto-apply. Cleared prompt removes
    the file. Best-effort, never raises.
    """
    if profile is None or not profile.path:
        return
    home = os.path.dirname(os.path.expanduser(profile.path))
    if not os.path.isdir(home):
        return
    try:
        soul_path = os.path.join(home, "SOUL.md")
        if profile.system_prompt and profile.system_prompt.strip():
            with open(soul_path, "w", encoding="utf-8") as f:
                f.write(profile.system_prompt)
            logger.info("Synced SOUL.md for profile %s", getattr(profile, "handle", "?"))
        elif os.path.isfile(soul_path):
            os.remove(soul_path)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to sync SOUL.md for profile %s", getattr(profile, "handle", "?"), exc_info=True)
