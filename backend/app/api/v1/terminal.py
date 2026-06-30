"""Web Terminal: WebSocket PTY for browser-based shell access.

Security hardening:
- Filters environment variables (whitelist only safe ones, never SECRET_KEY etc.)
- Sets the shell's initial working directory to a per-user workspace under
  settings.workspace_root (NOT a filesystem jail — the shell can `cd` anywhere the
  host user can access; real isolation is a deployment concern, see
  agent_runner/sandbox.py and docs/方案设计.md §12)
- Audit-logs session start/end
"""
from __future__ import annotations

import asyncio
import codecs
import fcntl
import logging
import os
import pty
import signal
import struct
import termios

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter()
logger = logging.getLogger("hermes.terminal")

#: Environment variables safe to pass to the terminal subprocess.
#: NEVER include SECRET_KEY, DATABASE_URL, REDIS_URL, MINIO_SECRET_KEY, etc.
_SAFE_ENV_KEYS = frozenset({
    "TERM", "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "SHELL",
    "USER", "LOGNAME", "EDITOR", "VISUAL", "PAGER", "LESS",
})

#: Initial terminal size — matches the frontend xterm.js default (TerminalView.vue).
_INITIAL_ROWS = 24
_INITIAL_COLS = 100


def _build_safe_env() -> dict[str, str]:
    """Build a filtered environment for the terminal subprocess."""
    safe: dict[str, str] = {}
    for key, val in os.environ.items():
        if key in _SAFE_ENV_KEYS or key.startswith(("TERM_", "LC_")):
            safe[key] = val
    safe["TERM"] = "xterm-256color"
    return safe


def _spawn_pty_shell(shell: str, cwd: str) -> tuple[int, int]:
    """Fork a shell attached to a real pseudo-terminal.

    Uses `pty.fork()` rather than `subprocess` + a manually-passed slave fd:
    pty.fork() handles session creation, controlling-tty assignment, and fd
    wiring atomically in the forked child, which sidesteps the well-known
    footgun of doing that by hand via Popen's preexec_fn (fd ordering vs.
    close_fds is fragile and easy to get subtly wrong).

    Returns (pid, master_fd). In the child, this never returns — it execs.
    """
    pid, master_fd = pty.fork()
    if pid == 0:
        # Child: real pty is already wired to fd 0/1/2 by pty.fork().
        try:
            os.chdir(cwd)
            os.execvpe(shell, [shell, "-i"], _build_safe_env())
        except Exception:
            os._exit(1)
    # Parent
    try:
        fcntl.ioctl(
            master_fd, termios.TIOCSWINSZ,
            struct.pack("HHHH", _INITIAL_ROWS, _INITIAL_COLS, 0, 0),
        )
    except OSError:
        pass
    return pid, master_fd


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

    # Per-user workspace directory (initial cwd only — see module docstring).
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

    loop = asyncio.get_event_loop()
    pid: int | None = None
    master_fd: int | None = None

    try:
        pid, master_fd = await loop.run_in_executor(None, _spawn_pty_shell, shell, ws_dir)
        os.set_blocking(master_fd, False)

        output_queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _on_master_readable():
            try:
                data = os.read(master_fd, 4096)
            except BlockingIOError:
                return
            except OSError:
                # EIO is the normal Linux signal that the slave side closed (child exited).
                data = b""
            output_queue.put_nowait(data)
            if not data:
                try:
                    loop.remove_reader(master_fd)
                except (ValueError, OSError):
                    pass

        loop.add_reader(master_fd, _on_master_readable)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        async def read_output():
            """Bridge pty master output to the WebSocket, decoding incrementally
            so multi-byte UTF-8 sequences split across reads aren't mangled."""
            try:
                while True:
                    data = await output_queue.get()
                    if not data:
                        break
                    text = decoder.decode(data)
                    if text:
                        await websocket.send_text(text)
            except (WebSocketDisconnect, ConnectionResetError):
                pass
            except Exception:
                pass

        async def write_input():
            """Read from WebSocket and write to the pty master (kernel tty driver
            handles control bytes like Ctrl-C as real signals via the real pty)."""
            try:
                while True:
                    msg = await websocket.receive_text()
                    try:
                        os.write(master_fd, msg.encode("utf-8"))
                    except OSError:
                        break
            except WebSocketDisconnect:
                pass
            except Exception:
                pass

        # Run output/input bridging concurrently; stop on the first to finish
        # (read_output ends on EOF/EIO once the child exits — see _on_master_readable).
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(read_output()),
                asyncio.create_task(write_input()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

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
        if master_fd is not None:
            try:
                loop.remove_reader(master_fd)
            except (ValueError, OSError):
                pass
            try:
                os.close(master_fd)
            except OSError:
                pass

        # Clean up the child process.
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, os.waitpid, pid, 0), timeout=3.0
                    )
                except asyncio.TimeoutError:
                    os.kill(pid, signal.SIGKILL)
                    await loop.run_in_executor(None, os.waitpid, pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
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
