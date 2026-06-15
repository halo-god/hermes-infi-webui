
# Agent Runner image — the ACP gateway. The discovery scan lights up any agent
# CLI present on PATH. To use the real NousResearch Hermes Agent CLI, build with
#   --build-arg INSTALL_HERMES=true
# (requires network at build time; the CLI then runs as `hermes acp`). Without
# it, the runner falls back to the bundled mock ACP agent so the stack works
# fully offline for development.
FROM docker.m.daocloud.io/library/python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip \
    && pip install \
        "sqlalchemy[asyncio]>=2.0.30" "asyncpg>=0.29" \
        "pydantic>=2.7" "pydantic-settings>=2.3" "pydantic[email]>=2.7" \
        "pyjwt>=2.8" "argon2-cffi>=23.1" "redis>=5.0" "boto3>=1.34" \
        "prometheus-client>=0.20"

# ── (optional) install the real Hermes Agent CLI ──
# Enable with: docker build --build-arg INSTALL_HERMES=true
ARG INSTALL_HERMES=false
ENV PATH="/home/hermes/.local/bin:${PATH}"
RUN if [ "$INSTALL_HERMES" = "true" ]; then \
        curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash \
        && (hermes --version || true) ; \
    fi

COPY backend/ ./
RUN chmod +x scripts/run-agent.sh scripts/start.sh 2>/dev/null || true

# Create non-root user for security
RUN groupadd -r hermes && useradd -r -g hermes -d /app -s /sbin/nologin hermes \
    && chown -R hermes:hermes /app

USER hermes

CMD ["./scripts/run-agent.sh"]
