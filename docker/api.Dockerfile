FROM docker.m.daocloud.io/library/python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: curl for healthcheck; LibreOffice headless for Office -> PDF
# preview conversion (.doc/.xls/.ppt/.docx/.xlsx/.pptx/ODF). fonts-noto-cjk
# is required — without it Chinese text renders as boxes in the converted
# PDFs. Sized with --no-install-recommends to keep the image lean (~+600MB
# layer); help/templates/gallery are dropped (never needed for conversion).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/lib/libreoffice/share/help \
    && rm -rf /usr/lib/libreoffice/share/template \
    && rm -rf /usr/lib/libreoffice/share/gallery \
    && rm -rf /usr/share/doc /usr/share/man

# Install Python deps from pyproject.toml for reproducible builds.
COPY backend/pyproject.toml ./
COPY backend/app ./app
# torch CPU-only first: sentence-transformers (RAG) is a hard dep, but the
# default Linux wheel drags ~2.5GB of CUDA libs the api container never uses.
# Installing the CPU wheel up front makes `pip install .` reuse it.
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install .

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
