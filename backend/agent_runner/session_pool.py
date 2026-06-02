"""Per-conversation ACP subprocess pool.

One long-lived ACP session per conversation preserves the agent's context
across turns. Callbacks are rebound per prompt; prompts for a given
conversation are processed sequentially by the runner.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from agent_runner.acp_client import ACPClient, ACPTimeout, OnFsWrite, OnUpdate

logger = logging.getLogger("hermes.pool")

# Timeouts for pool operations (seconds)
POOL_START_TIMEOUT = 30
POOL_INIT_TIMEOUT = 30
POOL_SESSION_TIMEOUT = 30


class SessionPool:
    def __init__(self) -> None:
        self._clients: dict[str, ACPClient] = {}

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
    ) -> tuple[ACPClient, str | None]:
        """Return (client, new_session_id_or_None). session id is set only when
        a fresh subprocess+session was created."""
        c = self._clients.get(conversation_id)
        if c is not None and self._alive(c):
            c.on_update = on_update
            c.on_fs_write = on_fs_write
            return c, None

        # Drop stale client if any
        if c is not None:
            await self.drop(conversation_id)

        c = ACPClient(
            command,
            cwd,
            protocol_version=settings.acp_protocol_version,
            on_update=on_update,
            on_fs_write=on_fs_write,
        )
        try:
            await asyncio.wait_for(c.start(), timeout=POOL_START_TIMEOUT)
            await asyncio.wait_for(c.initialize(), timeout=POOL_INIT_TIMEOUT)
            if settings.hermes_acp_auth_method:
                await asyncio.wait_for(
                    c.authenticate(settings.hermes_acp_auth_method), timeout=POOL_INIT_TIMEOUT,
                )
            session_id = await asyncio.wait_for(c.new_session(cwd), timeout=POOL_SESSION_TIMEOUT)
        except (asyncio.TimeoutError, Exception) as exc:
            logger.error("Failed to create ACP session for %s: %s", conversation_id, exc)
            await c.stop()
            raise
        self._clients[conversation_id] = c
        return c, session_id

    async def drop(self, conversation_id: str) -> None:
        c = self._clients.pop(conversation_id, None)
        if c:
            await c.stop()

    async def close_all(self) -> None:
        for c in list(self._clients.values()):
            await c.stop()
        self._clients.clear()
