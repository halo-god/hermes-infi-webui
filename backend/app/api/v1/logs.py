"""Log viewer API — read rotating log files with filtering."""
from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.guards import require_admin
from app.core.logging import LOG_DIR
from app.db.models.user import User

router = APIRouter()

#: Regex to parse our log format:
#: "2026-06-25 14:30:00 INFO    [abc123] app.api.v1.auth — Login success"
_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"\[([^\]]*)\]\s+"
    r"([^\s]+)\s+—\s+(.*)$"
)

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LEVEL_ORDER = {lvl: i for i, lvl in enumerate(LEVELS)}


class LogEntry(BaseModel):
    timestamp: str
    level: str
    request_id: str
    logger: str
    message: str


class LogListResponse(BaseModel):
    entries: list[LogEntry]
    total: int


def _read_log_tail(limit: int = 500) -> list[str]:
    """Read the last N lines from the log files (newest first)."""
    log_dir = Path(LOG_DIR)
    if not log_dir.is_dir():
        return []

    # Collect log files: hermes.log + rotated backups (hermes.log.1, .2, ...)
    files = sorted(
        [f for f in log_dir.glob("hermes.log*") if f.is_file()],
        key=lambda f: (0, "") if f.name == "hermes.log" else (1, f.suffix),
    )

    lines: deque[str] = deque(maxlen=limit)
    for log_file in files:
        try:
            with open(log_file, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    lines.append(line.rstrip("\n"))
                    if len(lines) >= limit:
                        break
        except OSError:
            continue
        if len(lines) >= limit:
            break

    # Reverse to newest-first.
    return list(reversed(lines))


def _parse_line(line: str) -> LogEntry | None:
    """Parse a log line into a LogEntry, or None if it doesn't match."""
    m = _LOG_RE.match(line)
    if not m:
        return None
    return LogEntry(
        timestamp=m.group(1),
        level=m.group(2),
        request_id=m.group(3),
        logger=m.group(4),
        message=m.group(5),
    )


@router.get("/logs", response_model=LogListResponse)
async def get_logs(
    level: str | None = Query(None, description="Filter by log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)"),
    keyword: str | None = Query(None, description="Keyword search in message/logger"),
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(require_admin()),
):
    """Retrieve recent log entries with optional filtering."""
    raw_lines = _read_log_tail(limit * 3)  # Read more to account for filtering
    entries: list[LogEntry] = []

    min_level = LEVEL_ORDER.get(level.upper()) if level else None

    for line in raw_lines:
        entry = _parse_line(line)
        if entry is None:
            # Multi-line messages (tracebacks) — skip non-matching lines
            continue
        if min_level is not None and LEVEL_ORDER.get(entry.level, 0) < min_level:
            continue
        if keyword and keyword.lower() not in entry.message.lower() and keyword.lower() not in entry.logger.lower():
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break

    return LogListResponse(entries=entries, total=len(entries))
