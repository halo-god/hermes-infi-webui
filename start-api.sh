#!/bin/bash
# Start the Hermes Python FastAPI backend (port 8001 by default).
# Run from anywhere — script self-locates via BASH_SOURCE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/backend"
VENV_DIR="${VENV_DIR:-${BACKEND_DIR}/.venv}"

cd "${BACKEND_DIR}"

if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
  echo "ERROR: venv not found at ${VENV_DIR}" >&2
  echo "Run: cd ${BACKEND_DIR} && python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

export PYTHONUNBUFFERED=1
export HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
# Dev convenience: the default per-IP login rate limit (10/min) is a
# production guard; local E2E suites log in repeatedly (auth specs, member
# users) and trip 429s mid-run. Production deployments override via .env.
export LOGIN_RATE_LIMIT_PER_MIN="${LOGIN_RATE_LIMIT_PER_MIN:-1000}"

# Run alembic migrations if requested (HERMES_AUTO_MIGRATE=1)
if [[ "${HERMES_AUTO_MIGRATE:-0}" == "1" ]]; then
  echo "Running alembic upgrade head..."
  alembic upgrade head
fi

PORT="${HERMES_API_PORT:-8001}"
HOST="${HERMES_API_HOST:-0.0.0.0}"
LOG_LEVEL="${HERMES_API_LOG_LEVEL:-info}"

exec "${VENV_DIR}/bin/uvicorn" app.main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --log-level "${LOG_LEVEL}"
