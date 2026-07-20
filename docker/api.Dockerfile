FROM docker.m.daocloud.io/library/python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for asyncpg / argon2 builds are wheels-only; keep image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps from pyproject.toml for reproducible builds.
COPY backend/pyproject.toml backend/README.md ./
COPY backend/app ./app
RUN pip install --upgrade pip && pip install .

COPY backend/ ./

RUN chmod +x scripts/start.sh

# Create non-root user for security
RUN groupadd -r hermes && useradd -r -g hermes -d /app -s /sbin/nologin hermes \
    && chown -R hermes:hermes /app

USER hermes

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/v1/healthz || exit 1

CMD ["./scripts/start.sh"]
