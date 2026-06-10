"""Per-conversation ACP subprocess pool.

One long-lived ACP session per conversation preserves the agent's context
across turns. Callbacks are rebound per prompt; prompts for a given
conversation are processed sequentially by the runner.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings
from agent_runner.acp_client import ACPClient, ACPTimeout, OnFsWrite, OnUpdate, profile_env

logger = logging.getLogger("hermes.pool")

# Timeouts for pool operations (seconds)
POOL_START_TIMEOUT = 30
POOL_INIT_TIMEOUT = 60
POOL_SESSION_TIMEOUT = 30

# Evict sessions that have been idle for more than this many seconds (1 hour)
IDLE_TIMEOUT = 3600


class SessionPool:
    def __init__(self) -> None:
        self._clients: dict[str, ACPClient] = {}
        self._last_used: dict[str, float] = {}
        # profile_dir the client was spawned with — HERMES_HOME is fixed at
        # spawn, so a profile switch must drop and respawn the subprocess.
        self._profile_dirs: dict[str, str | None] = {}

    def _alive(self, c: ACPClient) -> bool:
        return (
            not c._closed
            and c._proc is not None
            and c._proc.returncode is None
        )

    async def get(
        self,
        conversation_id: str,
        command: list[str],
        cwd: str,
        on_update: OnUpdate,
        on_fs_write: OnFsWrite,
        acp_session_id: str | None = None,
        profile_dir: str | None = None,
    ) -> tuple[ACPClient, str | None]:
        """Return (client, new_session_id_or_None). session id is set only when
        a fresh subprocess+session was created."""
        self._last_used[conversation_id] = time.monotonic()
        c = self._clients.get(conversation_id)
        if c is not None and self._alive(c):
            if self._profile_dirs.get(conversation_id) == profile_dir:
                c.on_update = on_update
                c.on_fs_write = on_fs_write
                return c, None
            # Profile changed mid-conversation: the live subprocess is pinned to
            # the old HERMES_HOME, so respawn. The stored acp_session_id lives in
            # the old profile's session store — resume below fails and falls back
            # to a fresh session, which the runner persists.
            logger.info(
                "Profile changed for conv %s (%s -> %s), respawning agent",
                conversation_id[:8], self._profile_dirs.get(conversation_id), profile_dir,
            )
            await self.drop(conversation_id)
            c = None

        # Drop stale client if any
        if c is not None:
            await self.drop(conversation_id)

        # Build command
        effective_command = list(command)

        c = ACPClient(
            effective_command,
            cwd,
            protocol_version=settings.acp_protocol_version,
            on_update=on_update,
            on_fs_write=on_fs_write,
            env=profile_env(profile_dir),
        )
        try:
            await asyncio.wait_for(c.start(), timeout=POOL_START_TIMEOUT)
            init_result = await asyncio.wait_for(c.initialize(), timeout=POOL_INIT_TIMEOUT)
            if settings.hermes_acp_auth_method:
                await asyncio.wait_for(
                    c.authenticate(settings.hermes_acp_auth_method), timeout=POOL_INIT_TIMEOUT,
                )

            # Try to resume existing session first
            session_id = None
            if acp_session_id:
                # v0.16.0+: capabilities nested under agentCapabilities.sessionCapabilities
                # Legacy: loadSession at top level
                # NOTE: capability values are empty dicts {} (truthy=False), check key presence
                agent_caps = init_result.get("agentCapabilities") or init_result.get("agent_capabilities") or {}
                session_caps = agent_caps.get("sessionCapabilities") or agent_caps.get("session_capabilities") or {}
                supports_resume = (
                    "resume" in session_caps
                    or "loadSession" in agent_caps
                    or "load_session" in agent_caps
                    or "loadSession" in init_result
                )
                if supports_resume:
                    try:
                        await asyncio.wait_for(
                            c.resume_session(acp_session_id, cwd), timeout=POOL_SESSION_TIMEOUT,
                        )
                        session_id = None  # resumed, no new session
                        logger.info("Resumed ACP session %s for conv %s", acp_session_id[:8], conversation_id[:8])
                    except Exception as exc:
                        logger.warning("Resume failed for %s: %s, falling back to new", acp_session_id[:8], exc)
                        session_id = await asyncio.wait_for(c.new_session(cwd), timeout=POOL_SESSION_TIMEOUT)
                else:
                    # No resume support — create new session (don't use load,
                    # as it replays history and conflicts with our on_update callback)
                    session_id = await asyncio.wait_for(c.new_session(cwd), timeout=POOL_SESSION_TIMEOUT)
            else:
                session_id = await asyncio.wait_for(c.new_session(cwd), timeout=POOL_SESSION_TIMEOUT)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.error("Failed to create ACP session for %s: %s", conversation_id, exc)
            await c.stop()
            raise
        self._clients[conversation_id] = c
        self._profile_dirs[conversation_id] = profile_dir
        return c, session_id

    async def drop(self, conversation_id: str) -> None:
        c = self._clients.pop(conversation_id, None)
        self._last_used.pop(conversation_id, None)
        self._profile_dirs.pop(conversation_id, None)
        if c:
            await c.stop()

    async def evict_idle(self) -> None:
        """Drop sessions that have been idle longer than IDLE_TIMEOUT."""
        cutoff = time.monotonic() - IDLE_TIMEOUT
        stale = [cid for cid, t in self._last_used.items() if t < cutoff]
        for cid in stale:
            logger.info("Evicting idle session for conversation %s", cid[:8])
            await self.drop(cid)

    async def close_all(self) -> None:
        for c in list(self._clients.values()):
            await c.stop()
        self._clients.clear()
        self._last_used.clear()
        self._profile_dirs.clear()
