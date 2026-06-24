"""Agent discovery — scan PATH for known ACP CLIs (Hermes first).

Mirrors AionUi's approach: probe PATH for known binaries, register what's
present. Falls back to the bundled mock ACP agent so the platform is usable
before any real CLI is installed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

logger = logging.getLogger("hermes.discovery")

MOCK_AGENT_PATH = os.path.join(os.path.dirname(__file__), "mock_agent.py")

# Extra filesystem locations to probe (beyond PATH), ordered by likelihood.
_EXTRA_SEARCH_DIRS = [
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",          # macOS Homebrew (Apple Silicon)
    "/usr/local/opt/hermes/bin",  # macOS Homebrew (Intel)
    "/home/linuxbrew/.linuxbrew/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / "bin"),
    str(Path.home() / ".hermes" / "bin"),
]

# Try these binary names in order.
_HERMES_BIN_NAMES = ["hermes", "hermes-agent", "hermes-cli"]


@dataclass
class DiscoveredAgent:
    id: str
    label: str
    kind: str  # acp_cli | builtin_mock
    command: list[str]
    available: bool = True
    official: bool = False
    version: str | None = None
    color: str | None = "#b8852a"
    icon: str | None = "brand"
    description: str | None = None
    capabilities: dict = field(default_factory=dict)


# First-batch catalog. Hermes is the only real target for now; others are
# placeholders the scan will light up if their binaries appear on PATH.
KNOWN_AGENTS = [
    {
        "id": "hermes",
        "label": "Hermes Agent",
        "bin": settings.hermes_bin,
        "acp_args": settings.hermes_acp_args,  # `hermes acp`
        "official": True,
        "color": "#b8852a",
        "icon": "brand",
        "description": "NousResearch 自进化智能体，通过 ACP 连接本机会话",
    },
]


def find_hermes_binary() -> str | None:
    """Find the hermes binary using multiple strategies.

    Priority:
    1. Explicit HERMES_BIN env var / settings (if it's an absolute path that exists)
    2. shutil.which() on the configured name (honours $PATH)
    3. Probe extra well-known directories with several binary name variants
    """
    # 1. Explicit override
    cfg_bin = settings.hermes_bin
    if cfg_bin and os.path.isabs(cfg_bin) and os.path.isfile(cfg_bin):
        return cfg_bin

    # 2. PATH lookup on the configured name
    found = shutil.which(cfg_bin)
    if found:
        return found

    # 3. Probe extra directories with variant names
    candidate_names = [cfg_bin] + [n for n in _HERMES_BIN_NAMES if n != cfg_bin]
    for d in _EXTRA_SEARCH_DIRS:
        for name in candidate_names:
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

    return None


async def _probe_version(path: str) -> str | None:
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=6)
        text = out.decode("utf-8", "ignore").strip()
        return text.splitlines()[0][:64] if text else None
    except Exception:  # noqa: BLE001 — a CLI without --version may hang; don't leak it
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
        return None


async def scan() -> list[DiscoveredAgent]:
    """Async batch scan of PATH (faster than serial execSync, per AionUi)."""
    found: list[DiscoveredAgent] = []

    hermes_path = find_hermes_binary()

    probes = []
    for spec in KNOWN_AGENTS:
        if spec["id"] == "hermes":
            path = hermes_path
        else:
            path = shutil.which(spec["bin"])
        probes.append((spec, path))

    versions = await asyncio.gather(
        *[_probe_version(p) if p else _noop() for _, p in probes]
    )

    for (spec, path), version in zip(probes, versions):
        if path:
            found.append(
                DiscoveredAgent(
                    id=spec["id"],
                    label=spec["label"],
                    kind="acp_cli",
                    command=[path, *spec["acp_args"]],
                    available=True,
                    official=spec.get("official", False),
                    version=version,
                    color=spec.get("color"),
                    icon=spec.get("icon"),
                    description=spec.get("description"),
                )
            )
            logger.info("Discovered ACP agent: %s -> %s %s", spec["id"], path, spec["acp_args"])

    # Fallback: register built-in mock agents so the app (and roundtable) work
    # before any real CLI is installed. Distinct personas make roundtable visible.
    if not any(a.id == "hermes" for a in found) and settings.acp_allow_mock_fallback:
        mocks = [
            ("hermes", "Hermes Agent (mock)", "#b8852a", "brand", "综合协调", True),
            ("cowork", "Cowork (mock)", "#3a6da1", "users", "提出方案", False),
            ("critic", "Critic (mock)", "#b1463a", "shield", "提出风险", False),
        ]
        for aid, label, color, icon, stance, official in mocks:
            cmd = [sys.executable, MOCK_AGENT_PATH, "--persona", aid]
            if aid != "hermes":
                cmd.append("--no-file")  # only the lead produces the artifact
            found.append(
                DiscoveredAgent(
                    id=aid, label=label, kind="builtin_mock", command=cmd,
                    available=True, official=official, version="mock-0.1",
                    color=color, icon=icon, description=stance,
                )
            )
        logger.warning("hermes CLI not found on PATH — using bundled mock ACP agents")

    return found


async def _noop():
    return None


def _read_yaml(path: Path) -> dict:
    """Safely read a YAML config file; returns {} on any failure."""
    try:
        import yaml  # optional dep; only needed for fs profile scan

        with open(path) as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def get_hermes_home() -> Path:
    """Return the effective Hermes home directory.

    Resolution order:
    1. settings.hermes_home (env var HERMES_HOME)
    2. ~/.hermes
    """
    override = settings.hermes_home.strip() if settings.hermes_home else ""
    if override:
        return Path(override)
    return Path.home() / ".hermes"



def _extract_model_name(raw) -> str:
    """Extract model name string from config value.

    config.yaml 'model' can be either a plain string or a dict with 'default' key.
    """
    if isinstance(raw, dict):
        return raw.get('default', 'hermes-4')
    if isinstance(raw, str):
        return raw
    return 'hermes-4'

def list_hermes_fs_profiles() -> list[dict]:
    """Scan the Hermes home directory for profile config files.

    Supports two directory layouts:
      <hermes_home>/config.yaml                   → default profile
      <hermes_home>/{name}/config.yaml            → named profile (user format)
      <hermes_home>/profiles/{name}/config.yaml   → named profile (hermes-web-ui format)

    Returns a list of dicts with keys: name, handle, model, path.
    """
    hermes_home = get_hermes_home()
    results: list[dict] = []

    if not hermes_home.exists():
        logger.debug("Hermes home not found: %s", hermes_home)
        return results

    # Default profile
    default_cfg = hermes_home / "config.yaml"
    if default_cfg.exists():
        cfg = _read_yaml(default_cfg)
        results.append(
            {
                "name": cfg.get("alias", "默认助手"),
                "handle": "hermes-default",
                "model": _extract_model_name(cfg.get("model", "hermes-4")),
                "path": str(default_cfg),
            }
        )

    # Named profiles — scan both layout variants, de-duplicate by handle
    existing_handles = {r["handle"] for r in results}
    for search_dir in [hermes_home, hermes_home / "profiles"]:
        if not search_dir.is_dir():
            continue
        for child in sorted(search_dir.iterdir()):
            if not child.is_dir():
                continue
            cfg_file = child / "config.yaml"
            if not cfg_file.exists():
                continue
            handle = f"hermes-{child.name}"
            if handle in existing_handles:
                continue
            cfg = _read_yaml(cfg_file)
            results.append(
                {
                    "name": cfg.get("alias", child.name),
                    "handle": handle,
                    "model": _extract_model_name(cfg.get("model", "hermes-4")),
                    "path": str(cfg_file),
                }
            )
            existing_handles.add(handle)

    return results


async def probe_hermes_version() -> str:
    """Return the `hermes --version` string, or 'unknown' if not on PATH."""
    path = find_hermes_binary()
    if not path:
        return "unknown"
    ver = await _probe_version(path)
    return ver or "unknown"
