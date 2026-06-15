"""Web Terminal: WebSocket PTY for browser-based shell access."""
from __future__ import annotations

import asyncio
import os
import signal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter()


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

    await websocket.accept()

    # Determine shell
    shell = os.environ.get("SHELL", "/bin/bash")
    if not os.path.exists(shell):
        shell = "/bin/sh"

    process = None
    try:
        # Create subprocess with PTY-like behavior
        process = await asyncio.create_subprocess_exec(
            shell,
            "-i",  # interactive mode
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "TERM": "xterm-256color",
                "COLUMNS": "120",
                "LINES": "30",
            },
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

        try:
            await websocket.close()
        except Exception:
            pass
