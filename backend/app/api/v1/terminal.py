"""Web Terminal: WebSocket PTY for browser-based shell access.

Security hardening:
- Filters environment variables (whitelist only safe ones, never SECRET_KEY etc.)
- Confines working directory to a per-user workspace under settings.workspace_root
- Audit-logs session start/end
"""
from __future__ import annotations

import asyncio
import os
import signal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter()

#: Environment variables safe to pass to the terminal subprocess.
#: NEVER include SECRET_KEY, DATABASE_URL, REDIS_URL, MINIO_SECRET_KEY, etc.
_SAFE_ENV_KEYS = frozenset({
    "TERM", "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "SHELL",
    "USER", "LOGNAME", "EDITOR", "VISUAL", "PAGER", "LESS",
})


def _build_safe_env() -> dict[str, str]:
    """Build a filtered environment for the terminal subprocess."""
    safe: dict[str, str] = {}
    for key, val in os.environ.items():
        if key in _SAFE_ENV_KEYS or key.startswith(("TERM_", "LC_")):
            safe[key] = val
    safe["TERM"] = "xterm-256color"
    safe["COLUMNS"] = "120"
    safe["LINES"] = "30"
    return safe


@router.websocket("/terminal/ws")
async def terminal_ws(
    websocket: WebSocket,
):
    """WebSocket PTY terminal. Spawns a shell process and bridges I/O."""
    # Authenticate via a short-lived media ticket (WebSocket can't send headers,
    # and a raw access token in the URL would leak into logs/history).
    from app.core import redis as redis_core

    ticket = websocket.query_params.get("ticket")
    user_id = await redis_core.resolve_media_ticket(ticket)
    if not user_id:
        await websocket.close(code=4001, reason="Invalid ticket")
        return

    # Permission check: terminal.access (platform permission matrix).
    from app.db.base import async_session_maker
    from app.db.models.user import User
    from app.core.rbac import has_at_least
    from app.core.guards import _default_roles
    from app.services import settings_service, audit_service
    from app.config import settings

    import uuid as _uuid
    client_ip = websocket.client.host if websocket.client else None

    async with async_session_maker() as db:
        user = await db.get(User, _uuid.UUID(user_id))
        if not user:
            await websocket.close(code=4001, reason="User not found")
            return
        if not has_at_least(user.role, "super_admin"):
            s = await settings_service.get(db)
            overrides: dict = (s.data or {}).get("permission_overrides", {})
            roles = overrides.get("terminal.access") or _default_roles("terminal.access")
            if user.role not in roles:
                await websocket.close(code=4003, reason="无终端访问权限")
                return

    await websocket.accept()

    # Per-user workspace directory (confined cwd).
    ws_dir = os.path.join(settings.workspace_root, f"terminal-{user_id}")
    os.makedirs(ws_dir, exist_ok=True)

    # Audit-log session start.
    await audit_service.record(
        action="terminal.session.start",
        actor_id=_uuid.UUID(user_id),
        actor_name=getattr(user, "name", ""),
        target=ws_dir,
        ip=client_ip,
    )

    # Determine shell
    shell = os.environ.get("SHELL", "/bin/bash")
    if not os.path.exists(shell):
        shell = "/bin/sh"

    process = None
    try:
        # Create subprocess with filtered env + confined cwd.
        process = await asyncio.create_subprocess_exec(
            shell,
            "-i",  # interactive mode
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ws_dir,
            env=_build_safe_env(),
        )

        async def read_output():
            """Read from process stdout/stderr and send to WebSocket."""
            try:
                while True:
                    # Read from stdout
                    data = await process.stdout.read(1024)
                    if not data:
                        break
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
            except (WebSocketDisconnect, ConnectionResetError):
                pass
            except Exception:
                pass

        async def read_stderr():
            """Read from process stderr and send to WebSocket."""
            try:
                while True:
                    data = await process.stderr.read(1024)
                    if not data:
                        break
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
            except (WebSocketDisconnect, ConnectionResetError):
                pass
            except Exception:
                pass

        async def write_input():
            """Read from WebSocket and write to process stdin."""
            try:
                while True:
                    msg = await websocket.receive_text()
                    if process.stdin and not process.stdin.is_closing():
                        process.stdin.write(msg.encode("utf-8"))
                        await process.stdin.drain()
            except WebSocketDisconnect:
                # Clean shutdown
                if process.stdin and not process.stdin.is_closing():
                    process.stdin.close()
            except (ConnectionResetError, BrokenPipeError):
                pass
            except Exception:
                pass

        # Run all three tasks concurrently
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(read_output()),
                asyncio.create_task(read_stderr()),
                asyncio.create_task(write_input()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    except Exception as e:
        try:
            await websocket.send_text(f"\r\n[Terminal Error: {e}]\r\n")
        except Exception:
            pass
    finally:
        # Clean up process
        if process is not None:
            try:
                if process.returncode is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
            except Exception:
                pass

        # Audit-log session end.
        await audit_service.record(
            action="terminal.session.end",
            actor_id=_uuid.UUID(user_id),
            actor_name=getattr(user, "name", ""),
            target=ws_dir,
            ip=client_ip,
        )

        try:
            await websocket.close()
        except Exception:
            pass
