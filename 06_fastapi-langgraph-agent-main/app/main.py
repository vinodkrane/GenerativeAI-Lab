"""FastAPI application entry point.

Wires together: lifespan (DB/Redis connections + logging setup),
middleware stack (CORS, auth, request-id, access-logging, rate
limiting), exception handlers, and the API router.

Run with:
    uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.postgres import connect_postgres, disconnect_postgres
from app.db.redis import connect_redis, disconnect_redis
from app.middleware.auth import AuthMiddleware
from app.middleware.cors import cors_kwargs
from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from app.middleware.request_id import RequestIDMiddleware
from app.api.router import api_router

configure_logging()
log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app_starting", environment=settings.environment)
    await connect_redis()
    await connect_postgres()
    try:
        yield
    finally:
        await disconnect_postgres()
        await disconnect_redis()
        log.info("app_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- Rate limiting (slowapi) ---
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # --- Middleware stack ---
    # Order matters: Starlette applies middleware in reverse of add
    # order, i.e. the LAST one added runs FIRST on the request path.
    # We want: CORS -> RequestID -> Logging -> Auth (closest to routes),
    # so we add them in the opposite order below.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(CORSMiddleware, **cors_kwargs())

    # --- API router ---
    app.include_router(api_router)

    # --- Exception handlers ---
    register_exception_handlers(app)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
