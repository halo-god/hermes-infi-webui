"""FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import api_router
from app.config import settings
from app.core.logging import configure_logging, logger, request_id_var
from app.core.metrics import MetricsMiddleware, RequestIDMiddleware, TimeoutMiddleware, metrics_response
from app.core.redis import close_redis, get_redis
from app.core.tracing import setup_tracing, shutdown_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)

    # Setup OpenTelemetry tracing
    setup_tracing(service_name=settings.app_name)

    # Fail fast on insecure defaults in production/staging — never ship a
    # forgeable JWT secret, a well-known admin password, or debug mode.
    problems = settings.validate_for_production()
    if problems:
        msg = "Insecure configuration: " + "; ".join(problems)
        if settings.enforce_secure_config:
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
    shutdown_tracing()
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

    # ── Exception handlers ──────────────────────────────────────────────
    # Preserve FastAPI's normal HTTP/validation responses, but never let an
    # unhandled exception leak a Python traceback to the client. The full
    # error is logged server-side with the request's correlation id.
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "request_id": request_id_var.get()},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors(), "request_id": request_id_var.get()},
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误", "request_id": request_id_var.get()},
        )

    @app.get("/")
    async def root() -> dict:
        return {"app": settings.app_name, "version": "0.1.0", "docs": "/api/docs"}

    @app.get("/metrics")
    async def metrics():
        return metrics_response()

    return app


app = create_app()
