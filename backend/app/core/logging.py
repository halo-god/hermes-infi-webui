"""Structured-ish logging setup with per-request correlation IDs."""
from __future__ import annotations

import contextvars
import logging
import sys

from app.config import settings

# Bound to each request by RequestIDMiddleware; threads through every log line
# emitted while handling that request so logs can be correlated end-to-end.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


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

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIDFilter())
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)

    # Tame noisy libraries.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


logger = logging.getLogger("hermes")
