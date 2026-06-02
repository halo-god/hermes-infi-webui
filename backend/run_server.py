#!/usr/bin/env python3
"""Start uvicorn with log to file (clean, no duplicates)."""
import logging

LOG_FILE = "/tmp/hermes-api.log"

# App logger: file only (uvicorn loggers handle their own file output)
app_logger = logging.getLogger("hermes")
app_logger.setLevel(logging.INFO)
fh = logging.FileHandler(LOG_FILE, mode="a")
fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s - %(message)s"))
app_logger.addHandler(fh)

import uvicorn

# File handler added to:
# - uvicorn (catches uvicorn.error via propagation) 
# - uvicorn.access (standalone, propagate=false)
# NOT to uvicorn.error (would double-write via propagation)
log_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
        "file": {
            "format": "%(asctime)s %(levelname)-5s %(name)s - %(message)s",
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "formatter": "file",
            "class": "logging.FileHandler",
            "filename": LOG_FILE,
            "mode": "a",
        },
        "access_file": {
            "formatter": "access",
            "class": "logging.FileHandler",
            "filename": LOG_FILE,
            "mode": "a",
        },
    },
    "loggers": {
        "uvicorn": {
            "handlers": ["default", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.error": {
            "level": "INFO",
            "propagate": True,  # goes to parent uvicorn, no own handlers
        },
        "uvicorn.access": {
            "handlers": ["access", "access_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

uvicorn.run(
    "app.main:app",
    host="0.0.0.0",
    port=8000,
    log_level="info",
    access_log=True,
    log_config=log_config,
)
