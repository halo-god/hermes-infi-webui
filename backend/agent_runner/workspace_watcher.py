"""Filesystem watcher for MCP-generated files.

When an agent uses MCP write_file tools (bypassing ACP fs/write_text_file),
files land on disk directly. This watcher syncs them into the workspace DB
and emits SSE events so the frontend panel sees them.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.core.files import safe_relative_path
from agent_runner import storage

logger = logging.getLogger("hermes.watcher")

# Debounce: ignore events for N seconds after the last write for a path.
_DEBOUNCE_SECONDS = 0.5
# Grace period before starting sync after file creation (wait for write to finish).
_GRACE_SECONDS = 0.3
# Ignore files written by on_fs_write itself (to avoid double-syncing ACP writes).
_IGNORE_EXTENSIONS = {".tmp", ".swp", ".swx", ".part", ".bak", ".crdownload"}


def _should_ignore(path: str) -> bool:
    p = Path(path)
    if p.name.startswith(".") or p.name.startswith("~"):
        return True
    if p.suffix.lower() in _IGNORE_EXTENSIONS:
        return True
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: asyncio.Queue) -> None:
        self._queue = queue

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = event.src_path
        self._enqueue(src.decode("utf-8") if isinstance(src, bytes) else src)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = event.src_path
        self._enqueue(src.decode("utf-8") if isinstance(src, bytes) else src)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        dest = event.dest_path
        if dest:
            self._enqueue(dest.decode("utf-8") if isinstance(dest, bytes) else dest)

    def _enqueue(self, path: str) -> None:
        if _should_ignore(path):
            return
        try:
            self._queue.put_nowait(path)
        except Exception:
            pass


class WorkspaceWatcher:
    """Manages a watchdog Observer for a single conversation workspace."""

    def __init__(
        self,
        conversation_id: str,
        cwd: str,
        agent_id: str,
        message_id: str,
        get_current_msg_id: Callable[[], str],
        publish_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        self.conversation_id = conversation_id
        self.cwd = os.path.realpath(cwd)
        self.agent_id = agent_id
        self.message_id = message_id
        self.get_current_msg_id = get_current_msg_id
        self.publish_event = publish_event
        self._observer: Observer | None = None
        self._queue: asyncio.Queue | None = None
        self._consumer_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Handle] = {}
        self._synced: set[str] = set()
        self._lock = asyncio.Lock()
        self._shutdown = False

    def start(self) -> None:
        if self._observer is not None:
            return
        os.makedirs(self.cwd, exist_ok=True)
        self._queue = asyncio.Queue()
        handler = _Handler(self._queue)
        self._observer = Observer()
        self._observer.schedule(handler, self.cwd, recursive=True)
        self._observer.start()
        self._consumer_task = asyncio.create_task(self._consume_queue())
        logger.info(
            "Started workspace watcher for %s at %s",
            self.conversation_id[:8], self.cwd,
        )

    async def _consume_queue(self) -> None:
        """Consume paths from the watchdog thread queue and debounce them."""
        while not self._shutdown:
            try:
                path = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            real_path = os.path.realpath(path)
            if not real_path.startswith(self.cwd + os.sep):
                continue
            if _should_ignore(path):
                continue

            # Cancel any pending debounce for this path
            existing = self._pending.pop(real_path, None)
            if existing:
                existing.cancel()

            # Schedule a new debounced sync
            self._pending[real_path] = asyncio.get_event_loop().call_later(
                _DEBOUNCE_SECONDS + _GRACE_SECONDS,
                lambda p=real_path: asyncio.create_task(self._sync_path(p)),
            )

    async def _sync_path(self, path: str) -> None:
        async with self._lock:
            self._pending.pop(path, None)
            if path in self._synced:
                return
            self._synced.add(path)
            if len(self._synced) > 200:
                self._synced = set(list(self._synced)[-100:])

        try:
            content = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
        except UnicodeDecodeError:
            logger.debug("Skipping binary file: %s", path)
            return
        except Exception as exc:
            logger.warning("Failed to read watched file %s: %s", path, exc)
            return

        rel = os.path.relpath(path, self.cwd)
        rel = safe_relative_path(rel)
        msg_id = self.get_current_msg_id() or self.message_id

        try:
            f = await storage.save_file(
                uuid.UUID(self.conversation_id),
                rel,
                content,
                self.agent_id,
                uuid.UUID(msg_id),
            )
        except Exception:
            logger.exception("workspace_watcher failed to save file: %s", path)
            return

        logger.info("Watched file synced to workspace: %s (%d chars)", rel, len(content))

        try:
            await self.publish_event(
                {
                    "type": "file",
                    "message_id": msg_id,
                    "file_id": str(f.id),
                    "name": f.name,
                    "kind": f.kind,
                    "version": f.current_version,
                    "diff": None,
                }
            )
        except Exception:
            logger.exception("Failed to publish file event for watched file")

    async def scan_existing(self) -> None:
        """Scan the workspace directory for existing files and sync them to DB."""
        logger.info("Scanning existing files for %s", self.conversation_id[:8])
        synced_count = 0
        for root, _dirs, files in os.walk(self.cwd):
            for name in files:
                path = os.path.join(root, name)
                if _should_ignore(path):
                    continue
                real_path = os.path.realpath(path)
                if not real_path.startswith(self.cwd + os.sep):
                    continue

                try:
                    content = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                except Exception as exc:
                    logger.debug("Skipping %s during scan: %s", path, exc)
                    continue

                rel = os.path.relpath(path, self.cwd)
                rel = safe_relative_path(rel)
                msg_id = self.get_current_msg_id() or self.message_id

                try:
                    f = await storage.save_file(
                        uuid.UUID(self.conversation_id),
                        rel,
                        content,
                        self.agent_id,
                        uuid.UUID(msg_id),
                    )
                except Exception:
                    logger.exception("scan_existing failed to save file: %s", path)
                    continue

                synced_count += 1
                logger.info("Scanned file synced to workspace: %s", rel)

                try:
                    await self.publish_event(
                        {
                            "type": "file",
                            "message_id": msg_id,
                            "file_id": str(f.id),
                            "name": f.name,
                            "kind": f.kind,
                            "version": f.current_version,
                            "diff": None,
                        }
                    )
                except Exception:
                    logger.exception("Failed to publish file event for scanned file")

        logger.info(
            "Scan complete for %s: %d file(s) synced",
            self.conversation_id[:8], synced_count,
        )

    def stop(self) -> None:
        self._shutdown = True
        if self._consumer_task:
            self._consumer_task.cancel()
            self._consumer_task = None
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        logger.info("Stopped workspace watcher for %s", self.conversation_id[:8])
