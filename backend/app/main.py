"""FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.config import settings
from app.core.logging import configure_logging, logger
from app.core.metrics import MetricsMiddleware, RequestIDMiddleware, TimeoutMiddleware, metrics_response
from app.core.redis import close_redis, get_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)

    # Fail fast on insecure defaults in production — never ship a forgeable
    # JWT secret or a well-known admin password.
    problems = settings.validate_for_production()
    if problems:
        msg = "Insecure configuration: " + "; ".join(problems)
        if settings.is_production:
            raise RuntimeError(msg)
        logger.warning("%s (allowed in %s)", msg, settings.environment)

    try:
        await get_redis().ping()
        logger.info("Redis connected")
    except Exception as exc:  # noqa: BLE001
        # Redis is on every hot path (streaming, rate limits, sessions); in
        # production a missing Redis means silent failures later — fail fast.
        if settings.is_production:
            raise RuntimeError(f"Redis unreachable at startup: {exc}") from exc
        logger.warning("Redis not reachable at startup: %s", exc)
    yield
    await close_redis()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(TimeoutMiddleware, timeout_seconds=30)
    # Outermost so the correlation id is bound before anything else logs.
    app.add_middleware(RequestIDMiddleware)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/")
    async def root() -> dict:
        return {"app": settings.app_name, "version": "0.1.0", "docs": "/api/docs"}

    @app.get("/metrics")
    async def metrics():
        return metrics_response()

    return app


app = create_app()
