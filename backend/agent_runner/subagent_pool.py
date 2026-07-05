"""In-process registry of live background-subagent ACP clients.

Structurally parallel to SessionPool (session_pool.py), but keyed by
subagent_id (not conversation_id) and tracking idle/max-lifetime deadlines
instead of a single IDLE_TIMEOUT — a persistent subagent is expected to sit
idle between user follow-ups for much longer than a normal chat turn before
eviction, and has a hard lifetime cap regardless of activity.

This pool only holds live subprocess handles for the current runner process.
Status/metadata survives a restart in the `background_subagents` table (see
app/db/models/subagent.py); the live client does not — see
reconcile_background_subagents() in runner_subagent.py.
"""
from __future__ import annotations

import logging
import time

from agent_runner.acp_client import ACPClient

logger = logging.getLogger("hermes.subagent_pool")


class SubagentPool:
    def __init__(self) -> None:
        self._clients: dict[str, ACPClient] = {}
        self._last_active: dict[str, float] = {}   # monotonic
        self._spawned_at: dict[str, float] = {}    # monotonic
        self._idle_timeout: dict[str, int] = {}
        self._max_lifetime: dict[str, int] = {}

    def register(
        self, subagent_id: str, client: ACPClient, *, idle_timeout: int, max_lifetime: int,
    ) -> None:
        now = time.monotonic()
        self._clients[subagent_id] = client
        self._last_active[subagent_id] = now
        self._spawned_at[subagent_id] = now
        self._idle_timeout[subagent_id] = idle_timeout
        self._max_lifetime[subagent_id] = max_lifetime

    def _alive(self, c: ACPClient) -> bool:
        return not c._closed and c._proc is not None and c._proc.returncode is None

    def get(self, subagent_id: str) -> ACPClient | None:
        c = self._clients.get(subagent_id)
        if c is None or not self._alive(c):
            return None
        return c

    def touch(self, subagent_id: str) -> None:
        if subagent_id in self._clients:
            self._last_active[subagent_id] = time.monotonic()

    def ids(self) -> list[str]:
        return list(self._clients.keys())

    async def drop(self, subagent_id: str) -> None:
        c = self._clients.pop(subagent_id, None)
        self._last_active.pop(subagent_id, None)
        self._spawned_at.pop(subagent_id, None)
        self._idle_timeout.pop(subagent_id, None)
        self._max_lifetime.pop(subagent_id, None)
        if c:
            await c.stop()

    def expiry_reason(self, subagent_id: str) -> str | None:
        """Return 'max_lifetime' or 'idle' if this subagent should be evicted
        right now, else None. max_lifetime takes priority since it's the
        harder, non-negotiable cap."""
        now = time.monotonic()
        spawned = self._spawned_at.get(subagent_id)
        last = self._last_active.get(subagent_id)
        if spawned is None or last is None:
            return None
        if now - spawned > self._max_lifetime.get(subagent_id, 14400):
            return "max_lifetime"
        if now - last > self._idle_timeout.get(subagent_id, 900):
            return "idle"
        return None

    async def evict_expired(self) -> list[tuple[str, str]]:
        """Drop subagents past their idle/max-lifetime deadline.

        Returns [(subagent_id, reason), ...] for the caller to finalize
        (publish nudge, update DB status) — this pool only owns the live
        subprocess handle, not DB/Redis state.
        """
        expired = [(sid, reason) for sid in list(self._clients) if (reason := self.expiry_reason(sid))]
        for sid, _ in expired:
            await self.drop(sid)
        return expired

    async def close_all(self) -> None:
        for c in list(self._clients.values()):
            await c.stop()
        self._clients.clear()
        self._last_active.clear()
        self._spawned_at.clear()
        self._idle_timeout.clear()
        self._max_lifetime.clear()
