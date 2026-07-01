FROM docker.m.daocloud.io/library/python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for asyncpg / argon2 builds are wheels-only; keep image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching).
COPY backend/pyproject.toml ./pyproject.toml
RUN pip install --upgrade pip \
    && pip install \
        "fastapi>=0.115" "uvicorn[standard]>=0.30" "gunicorn>=22.0" \
        "sqlalchemy[asyncio]>=2.0.30" "asyncpg>=0.29" "alembic>=1.13" \
        "pydantic>=2.7" "pydantic-settings>=2.3" "pydantic[email]>=2.7" \
        "pyjwt>=2.8" "argon2-cffi>=23.1" "redis>=5.0" \
        "python-multipart>=0.0.9" "httpx>=0.27" "boto3>=1.34" \
        "ldap3>=2.9" "prometheus-client>=0.20" \
        "python-docx>=1.1" "openpyxl>=3.1" "python-pptx>=1.0"

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
