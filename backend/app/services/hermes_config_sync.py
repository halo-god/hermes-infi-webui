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
    """Build the config dict from our settings to merge into hermes config.yaml."""
    return {
        "prompt_caching": {"cache_ttl": settings.hermes_prompt_cache_ttl},
        "terminal": {
            "backend": settings.hermes_terminal_backend,
            "persistent_shell": settings.hermes_persistent_shell,
        },
        "compression": {"enabled": settings.hermes_compression_enabled},
        "tool_output": {"max_bytes": settings.hermes_tool_output_max_bytes},
        "privacy": {"redact_pii": settings.hermes_redact_pii},
        "reasoning_effort": settings.hermes_reasoning_effort,
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
        merged = _deep_merge(existing, overrides)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(merged, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return True
    except Exception:
        logger.debug("Failed to sync config to %s", config_path, exc_info=True)
        return False


def sync_hermes_configs() -> dict:
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

    # 2. All profile configs (~/.hermes/profiles/*/config.yaml)
    profiles_dir = os.path.join(hermes_home, "profiles")
    if os.path.isdir(profiles_dir):
        for entry in os.listdir(profiles_dir):
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
