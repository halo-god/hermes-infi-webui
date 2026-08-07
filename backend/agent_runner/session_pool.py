"""Per-conversation ACP subprocess pool.

One long-lived ACP session per conversation preserves the agent's context
across turns. Callbacks are rebound per prompt; prompts for a given
conversation are processed sequentially by the runner.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable

from app.config import settings
from agent_runner.acp_client import ACPClient, OnFsWrite, OnUpdate, profile_env

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
        self._profile_dirs: dict[str, str | None] = {}
        self._mcp_servers: dict[str, list | None] = {}
        # Warm pool: pre-initialized ACP clients keyed by profile_dir.
        # Eliminates spawn+init cold start for new conversations.
        self._warm_pool: dict[str | None, deque[ACPClient]] = {}
        self._warm_lock = asyncio.Lock()
        # Per-profile warm-up outcome: {profile_dir: {"ok": bool, "error": str|None}}.
        # Fed to the admin 健康检查 console to show each Profile's ACP availability.
        self._warm_results: dict[str | None, dict] = {}
        self._agents: dict = {}  # set by Runner before warmup()

    def _alive(self, c: ACPClient) -> bool:
        return (
            not c._closed
            and c._proc is not None
            and c._proc.returncode is None
        )

    # ── Warm pool: pre-initialized clients for fast cold-start ──

    def set_agents(self, agents: dict) -> None:
        """Called by Runner after agent discovery, before warmup()."""
        self._agents = agents

    async def _spawn_warm_one(self, command: list[str], cwd: str, profile_dir: str | None = None) -> ACPClient | None:
        """Spawn + initialize a client (no session yet). Returns None on failure."""
        c = ACPClient(
            list(command), cwd,
            protocol_version=settings.acp_protocol_version,
            on_update=lambda u: None, on_fs_write=lambda p, c2: None,
            env=profile_env(profile_dir),
        )
        try:
            await asyncio.wait_for(c.start(), timeout=POOL_START_TIMEOUT)
            init_result = await asyncio.wait_for(c.initialize(), timeout=POOL_INIT_TIMEOUT)
            # Bug 9: apply auth if configured (same as cold path in get()).
            if settings.hermes_acp_auth_method:
                await asyncio.wait_for(
                    c.authenticate(settings.hermes_acp_auth_method), timeout=POOL_INIT_TIMEOUT,
                )
            c._init_result = init_result  # cached for resume capability check
            return c
        except Exception:  # noqa: BLE001
            logger.warning("Warm pool: failed to spawn a warm client", exc_info=True)
            try:
                await c.stop()
            except Exception:
                pass
            return None

    async def warmup(self) -> None:
        """Pre-spawn idle clients at Runner startup. Warms the default profile
        (None) plus any active profiles found in the DB so most conversations
        hit a warm client. Non-blocking on failure."""
        n = settings.session_pool_warm_size
        # Allow runtime override via SystemSettings.data.runner / hermes config.
        try:
            from app.services import settings_service
            from app.db.base import async_session_maker
            async with async_session_maker() as db:
                s = await settings_service.get(db)
                data = s.data or {}
                runner_cfg = data.get("runner", {})
                if "warm_pool_size" in runner_cfg:
                    n = int(runner_cfg["warm_pool_size"])
                # Apply hermes advanced config overrides from DB → settings.
                hermes_cfg = data.get("hermes", {})
                if hermes_cfg:
                    if "prompt_cache_ttl" in hermes_cfg:
                        settings.hermes_prompt_cache_ttl = hermes_cfg["prompt_cache_ttl"]
                    if "terminal_backend" in hermes_cfg:
                        settings.hermes_terminal_backend = hermes_cfg["terminal_backend"]
                    if "persistent_shell" in hermes_cfg:
                        settings.hermes_persistent_shell = hermes_cfg["persistent_shell"]
                    if "reasoning_effort" in hermes_cfg:
                        settings.hermes_reasoning_effort = hermes_cfg["reasoning_effort"]
                    if "compression_enabled" in hermes_cfg:
                        settings.hermes_compression_enabled = hermes_cfg["compression_enabled"]
                    if "tool_output_max_bytes" in hermes_cfg:
                        settings.hermes_tool_output_max_bytes = int(hermes_cfg["tool_output_max_bytes"])
                    if "redact_pii" in hermes_cfg:
                        settings.hermes_redact_pii = hermes_cfg["redact_pii"]
                    if "skills_sync_enabled" in hermes_cfg:
                        settings.hermes_skills_sync_enabled = hermes_cfg["skills_sync_enabled"]
                    logger.info("Applied hermes config overrides from DB: %s", list(hermes_cfg.keys()))
        except Exception:
            logger.debug("Warm pool: could not read config from DB", exc_info=True)

        # Write the final config values to hermes config.yaml files so they
        # persist across restarts and survive agent self-edits.
        try:
            from app.services.hermes_config_sync import sync_hermes_configs
            result = sync_hermes_configs()
            if result.get("synced"):
                logger.info("Hermes config.yaml synced: %s", ", ".join(result["synced"]))
        except Exception:
            logger.debug("Hermes config.yaml sync failed", exc_info=True)

        if n <= 0 or not self._agents:
            return
        agent = self._agents.get("hermes")
        if agent is None:
            return
        cwd = os.path.join(settings.workspace_root, "_warmup")
        os.makedirs(cwd, exist_ok=True)

        # Collect profile_dirs to warm: default (None) + active profiles from DB.
        profile_dirs: list[str | None] = [None]
        try:
            from app.db.base import async_session_maker
            from app.db.models.agent import Profile
            from sqlalchemy import select
            async with async_session_maker() as db:
                rows = (await db.execute(
                    select(Profile.path).where(Profile.is_active.is_(True))
                )).scalars().all()
                for path in rows:
                    if path:
                        import os as _os
                        pd = _os.path.dirname(_os.path.expanduser(path))
                        if pd not in profile_dirs:
                            profile_dirs.append(pd)
        except Exception:
            logger.debug("Warm pool: could not load profiles from DB", exc_info=True)

        logger.info("Warm pool: pre-spawning %d per profile for %d profile(s)...", n, len(profile_dirs))
        for pd in profile_dirs:
            for _ in range(n):
                c = await self._spawn_warm_one(agent.command, cwd, pd)
                if c is not None:
                    self._warm_pool.setdefault(pd, deque()).append(c)
                    self._warm_results[pd] = {"ok": True, "error": None}
                else:
                    # Remember the failure so the admin health check can flag
                    # this Profile's ACP as unavailable (bad HERMES_HOME / no
                    # LLM provider etc.).
                    self._warm_results[pd] = {
                        "ok": False,
                        "error": "ACP 预热失败（spawn/init 超时或异常）",
                    }
        total = sum(len(v) for v in self._warm_pool.values())
        logger.info("Warm pool: %d clients ready across %d profiles", total, len(self._warm_pool))

    async def _refill_warm_pool(self, profile_dir: str | None = None) -> None:
        """Replenish the warm pool for a given profile after a client is taken."""
        target = settings.session_pool_warm_size
        agent = self._agents.get("hermes")
        if agent is None or target <= 0:
            return
        async with self._warm_lock:
            dq = self._warm_pool.setdefault(profile_dir, deque())
            if len(dq) >= target:
                return
            cwd = os.path.join(settings.workspace_root, "_warmup")
            c = await self._spawn_warm_one(agent.command, cwd, profile_dir)
            if c is not None:
                dq.append(c)
                self._warm_results[profile_dir] = {"ok": True, "error": None}

    def pool_stats(self) -> dict:
        """Snapshot of the warm pool for the admin health check: target size,
        per-profile warm client count (alive only) and spawn outcome."""
        return {
            "target": settings.session_pool_warm_size,
            "per_profile": {
                (pd if pd is not None else "default"): {
                    "warm": sum(1 for c in dq if self._alive(c)),
                    "ok": bool((self._warm_results.get(pd) or {}).get("ok")),
                    "error": (self._warm_results.get(pd) or {}).get("error"),
                }
                for pd, dq in self._warm_pool.items()
            },
            "ts": time.time(),
        }

    async def get(
        self,
        conversation_id: str,
        command: list[str],
        cwd: str,
        on_update: OnUpdate,
        on_fs_write: OnFsWrite,
        acp_session_id: str | None = None,
        profile_dir: str | None = None,
        mcp_servers: list | None = None,
        session_namespace: str = "",
        on_permission_request: Callable[[str, dict], Awaitable[bool]] | None = None,
    ) -> tuple[ACPClient, str | None]:
        """Return (client, new_session_id_or_None). session id is set only when
        a fresh subprocess+session was created.

        ``session_namespace`` allows isolating multiple ACP sessions within the
        same conversation (e.g. one per profile in group chat) so that history
        from one assistant doesn't pollute another.
        """
        pool_key = f"{conversation_id}:{session_namespace}" if session_namespace else conversation_id
        self._last_used[pool_key] = time.monotonic()
        c = self._clients.get(pool_key)
        if c is not None and self._alive(c):
            if (
                self._profile_dirs.get(pool_key) == profile_dir
                and self._mcp_servers.get(pool_key) == mcp_servers
            ):
                c.on_update = on_update
                c.on_fs_write = on_fs_write
                c.on_permission_request = on_permission_request
                return c, None
            # Profile or MCP server set changed mid-conversation: mcpServers is
            # fixed at session/new time just like HERMES_HOME, so respawn. The
            # stored acp_session_id lives in the old session store — resume
            # below fails and falls back to a fresh session, which the runner
            # persists.
            logger.info(
                "Profile/MCP config changed for conv %s (ns=%s), respawning agent",
                conversation_id[:8], session_namespace,
            )
            await self.drop(pool_key)
            c = None

        # Drop stale client if any
        if c is not None:
            await self.drop(pool_key)

        # ── Warm pool fast path: if this is a default-config session
        # (no profile_dir, no MCP servers), grab a pre-initialized client
        # from the warm pool to skip the spawn+initialize cold start. ──
        c = None
        init_result = None
        if not mcp_servers:
            warm_deque = self._warm_pool.get(profile_dir)
            if warm_deque:
                try:
                    c = warm_deque.popleft()
                    if self._alive(c):
                        c.on_update = on_update
                        c.on_fs_write = on_fs_write
                        c.on_permission_request = on_permission_request
                        c.cwd = cwd
                        init_result = getattr(c, "_init_result", {}) or {}
                        logger.info(
                            "Warm pool hit for conv %s (ns=%s, profile=%s) - skipping spawn+init",
                            conversation_id[:8], session_namespace, profile_dir or "default",
                        )
                        asyncio.create_task(self._refill_warm_pool(profile_dir))
                    else:
                        try:
                            await c.stop()
                        except Exception:
                            pass
                        c = None
                except IndexError:
                    c = None

        # Cold path: spawn a fresh subprocess
        if c is None:
            effective_command = list(command)
            c = ACPClient(
                effective_command,
                cwd,
                protocol_version=settings.acp_protocol_version,
                on_update=on_update,
                on_fs_write=on_fs_write,
                env=profile_env(profile_dir),
                on_permission_request=on_permission_request,
            )
            try:
                await asyncio.wait_for(c.start(), timeout=POOL_START_TIMEOUT)
                init_result = await asyncio.wait_for(c.initialize(), timeout=POOL_INIT_TIMEOUT)
                if settings.hermes_acp_auth_method:
                    await asyncio.wait_for(
                        c.authenticate(settings.hermes_acp_auth_method), timeout=POOL_INIT_TIMEOUT,
                    )
            except (asyncio.TimeoutError, Exception) as exc:
                logger.error("Failed to spawn+init for %s (ns=%s): %s", conversation_id, session_namespace, exc)
                await c.stop()
                raise

        # ── Session creation / resume (shared by warm + cold paths) ──
        try:
            session_id = None
            if acp_session_id and init_result:
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
                            c.resume_session(acp_session_id, cwd, mcp_servers=mcp_servers),
                            timeout=POOL_SESSION_TIMEOUT,
                        )
                        session_id = None
                        logger.info("Resumed ACP session %s for conv %s (ns=%s)", acp_session_id[:8], conversation_id[:8], session_namespace)
                    except Exception as exc:
                        logger.warning("Resume failed for %s: %s, falling back to new", acp_session_id[:8], exc)
                        session_id = await asyncio.wait_for(
                            c.new_session(cwd, mcp_servers=mcp_servers), timeout=POOL_SESSION_TIMEOUT,
                        )
                else:
                    session_id = await asyncio.wait_for(
                        c.new_session(cwd, mcp_servers=mcp_servers), timeout=POOL_SESSION_TIMEOUT,
                    )
            else:
                session_id = await asyncio.wait_for(
                    c.new_session(cwd, mcp_servers=mcp_servers), timeout=POOL_SESSION_TIMEOUT,
                )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.error("Failed to create ACP session for %s (ns=%s): %s", conversation_id, session_namespace, exc)
            await c.stop()
            raise
        self._clients[pool_key] = c
        self._profile_dirs[pool_key] = profile_dir
        self._mcp_servers[pool_key] = mcp_servers
        return c, session_id

    async def drop(self, conversation_id: str, session_namespace: str = "") -> None:
        pool_key = f"{conversation_id}:{session_namespace}" if session_namespace else conversation_id
        c = self._clients.pop(pool_key, None)
        self._last_used.pop(pool_key, None)
        self._profile_dirs.pop(pool_key, None)
        self._mcp_servers.pop(pool_key, None)
        if c:
            # NOTE: no session/delete call here. "session/delete" is not part
            # of the ACP v1 schema (acp/meta.py AGENT_METHODS has no delete),
            # so the hermes agent answers -32601 Method not found — and in the
            # observed crash (turn dying mid-prompt with "Background task
            # failed (unknown method: session/delete)") the unknown-method
            # request path cascaded into a subprocess exit. Killing the
            # subprocess is sufficient cleanup; agent-side session state is
            # rebuilt on the next load/resume.
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
        self._mcp_servers.clear()
        # Clean up warm pool
        for dq in self._warm_pool.values():
            while dq:
                c = dq.popleft()
                try:
                    await c.stop()
                except Exception:
                    pass
        self._warm_pool.clear()
