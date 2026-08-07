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
from app.config import settings
from app.core import object_storage
from app.db.base import async_session_maker
from app.db.models.workspace import WorkspaceFile
from agent_runner import storage
from sqlalchemy import select

logger = logging.getLogger("hermes.watcher")

# Debounce: ignore events for N seconds after the last write for a path.
_DEBOUNCE_SECONDS = 0.5
# Grace period before starting sync after file creation (wait for write to finish).
_GRACE_SECONDS = 0.3
# Ignore files written by on_fs_write itself (to avoid double-syncing ACP writes).
_IGNORE_EXTENSIONS = {".tmp", ".swp", ".swx", ".part", ".bak", ".crdownload"}
# System-managed directories: dispatch writes user-uploaded attachments here so
# the agent can read_file them. These are NOT agent-authored files, so syncing
# them back to workspace_files would create duplicate records.
_IGNORE_DIRS = ("attachments",)


def _should_ignore(path: str) -> bool:
    p = Path(path)
    if p.name.startswith(".") or p.name.startswith("~"):
        return True
    if p.suffix.lower() in _IGNORE_EXTENSIONS:
        return True
    # Ignore system-managed directories (e.g. attachments/ is written by
    # dispatch, not by the agent — syncing it creates duplicate records).
    # Path may be absolute (watchdog gives absolute paths); check both the
    # raw parts and any "attachments" segment anywhere in the path.
    parts = p.parts
    if "attachments" in parts:
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

            # P6: a new write event means the file changed again — drop the
            # dedup marker so this second write re-syncs (otherwise the DB
            # content silently diverges from disk until the set truncates).
            self._synced.discard(real_path)

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
            if len(self._synced) > 200:
                self._synced = set(list(self._synced)[-100:])

        try:
            content = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
        except UnicodeDecodeError:
            # Binary files (images, PDFs, compiled output) can't be synced as
            # text — offload them to object storage so the workspace UI can
            # preview/download them, with the real kind/size recorded.
            await self._sync_binary(path)
            return
        except Exception as exc:
            logger.warning("Failed to read watched file %s: %s", path, exc)
            return

        # A freshly created file may still be being written (agent `cp` /
        # download): empty reads are retried once, and never create an empty
        # workspace row (which would render as a blank image in the UI).
        if not content or not content.strip():
            await asyncio.sleep(0.8)
            try:
                content = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
            except Exception:
                return
            if not content or not content.strip():
                logger.info("Watched file %s still empty after retry — skipping sync", path)
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

        async with self._lock:
            self._synced.add(path)

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

    async def _sync_binary(self, path: str) -> None:
        """Offload a binary file (image/PDF/...) to object storage so the
        workspace UI can preview it. Requires the minio backend — the inline
        text column can't hold bytes — otherwise just notify existence."""
        rel = os.path.relpath(path, self.cwd)
        rel = safe_relative_path(rel)
        kind = rel.rsplit(".", 1)[-1].lower() if "." in rel else "bin"
        msg_id = self.get_current_msg_id() or self.message_id

        if settings.storage_backend != "minio":
            try:
                await self.publish_event({
                    "type": "file",
                    "message_id": msg_id,
                    "file_id": None,
                    "name": os.path.basename(path),
                    "kind": kind,
                    "version": 1,
                    "diff": None,
                    "binary": True,
                    "workspace_path": rel,
                })
            except Exception:
                logger.debug("Failed to publish binary file event for %s", path, exc_info=True)
            return

        data = await asyncio.to_thread(Path(path).read_bytes)
        if not data:
            # still being written — retry once, then give up
            await asyncio.sleep(0.8)
            try:
                data = await asyncio.to_thread(Path(path).read_bytes)
            except Exception:
                return
            if not data:
                logger.info("Binary file %s still empty — skipping sync", path)
                return

        storage_key = f"{self.conversation_id}/{rel}"
        try:
            await asyncio.to_thread(
                object_storage.put, storage_key, data, storage.content_type_for(kind)
            )
        except Exception:
            logger.exception("Failed to store binary file %s to object storage", path)
            return

        async with async_session_maker() as db:
            res = await db.execute(
                select(WorkspaceFile).where(
                    WorkspaceFile.conversation_id == uuid.UUID(self.conversation_id),
                    WorkspaceFile.name == rel,
                )
            )
            f = res.scalar_one_or_none()
            if f is None:
                f = WorkspaceFile(
                    conversation_id=uuid.UUID(self.conversation_id),
                    message_id=uuid.UUID(msg_id),
                    name=rel,
                    kind=kind,
                    content=None,
                    storage_key=storage_key,
                    size_bytes=len(data),
                    created_by_agent=self.agent_id,
                    current_version=1,
                )
                db.add(f)
            else:
                # repair rows created from an empty read (blank image bug)
                f.storage_key = storage_key
                f.size_bytes = len(data)
                f.kind = kind
                f.content = None
            await db.commit()
            await db.refresh(f)

        async with self._lock:
            self._synced.add(path)

        logger.info("Binary file synced to workspace: %s (%d bytes)", rel, len(data))
        try:
            await self.publish_event({
                "type": "file",
                "message_id": msg_id,
                "file_id": str(f.id),
                "name": f.name,
                "kind": f.kind,
                "version": f.current_version,
                "diff": None,
            })
        except Exception:
            logger.debug("Failed to publish binary file event", exc_info=True)

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
                # Scanned files are pre-existing history, NOT artifacts of the
                # current turn — keep message_id unset so the API enrichment
                # (_enrich_messages_with_files) does not attach them to the
                # active message as if this turn produced them.
                msg_id = None

                try:
                    await storage.save_file(
                        uuid.UUID(self.conversation_id),
                        rel,
                        content,
                        self.agent_id,
                        msg_id,
                    )
                except Exception:
                    logger.exception("scan_existing failed to save file: %s", path)
                    continue

                synced_count += 1
                logger.info("Scanned file synced to workspace: %s", rel)
                # NOTE: no "file" event here. scan_existing runs at turn start
                # and would otherwise attach every historical workspace file to
                # the current message, flooding the message with stale file
                # chips. Only files actually written during this turn (runtime
                # _sync_path) publish events.

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
