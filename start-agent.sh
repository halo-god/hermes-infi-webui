#!/bin/bash
# Start the Hermes Python Agent Runner (ACP consumer).
# Run from anywhere — script self-locates via BASH_SOURCE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/backend"
VENV_DIR="${VENV_DIR:-${BACKEND_DIR}/.venv}"

cd "${BACKEND_DIR}"

if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
  echo "ERROR: venv not found at ${VENV_DIR}" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

export PYTHONUNBUFFERED=1

# All vars below are overridable. Defaults match docker-compose.yml.
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://hermes:hermes@localhost:5432/hermes}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export SECRET_KEY="${SECRET_KEY:-change-me-in-production-please-use-a-long-random-string}"
export WORKSPACE_ROOT="${WORKSPACE_ROOT:-${HOME}/hermes-data/workspaces}"
export HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes}"
export HERMES_BIN="${HERMES_BIN:-hermes}"
export HERMES_ACP_ARGS="${HERMES_ACP_ARGS:-[\"acp\"]}"
export ACP_ALLOW_MOCK_FALLBACK="${ACP_ALLOW_MOCK_FALLBACK:-true}"
export ACP_CONSUMER="${ACP_CONSUMER:-runner-1}"
export STORAGE_BACKEND="${STORAGE_BACKEND:-minio}"
export MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
export MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-hermes}"
export MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-hermes-minio-secret}"
export MINIO_BUCKET="${MINIO_BUCKET:-hermes-workspace}"
export SANDBOX_ENABLED="${SANDBOX_ENABLED:-false}"

exec "${VENV_DIR}/bin/python" -m agent_runner.runner
