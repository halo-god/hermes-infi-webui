"""Structured-ish logging setup with per-request correlation IDs."""
from __future__ import annotations

import contextvars
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from app.config import settings

# Bound to each request by RequestIDMiddleware; threads through every log line
# emitted while handling that request so logs can be correlated end-to-end.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

#: Directory where log files are written.
LOG_DIR = os.path.join(settings.workspace_root, "logs")
LOG_FILE = os.path.join(LOG_DIR, "hermes.log")

#: The format string used for both stdout and file handlers.
LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s — %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    level = logging.DEBUG if settings.debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on uvicorn reload.
    if root.handlers:
        return

    fmt = logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFMT)
    rid_filter = _RequestIDFilter()

    # Stdout handler (always on).
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.addFilter(rid_filter)
    stdout_handler.setFormatter(fmt)
    root.addHandler(stdout_handler)

    # File handler (rotating, 10MB × 5 files) — enables the /logs API.
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        file_handler.addFilter(rid_filter)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        # If the log directory is not writable (e.g. read-only container),
        # fall back to stdout-only logging.
        pass

    # Tame noisy libraries.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


logger = logging.getLogger("hermes")
