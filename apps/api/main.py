"""MetaScale FastAPI application factory.

Run locally:  uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from common.config import Settings, get_settings
from common.exceptions import MetaScaleError
from common.logging import configure_logging, get_logger, request_id_var
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.dependencies import Platform, get_platform

logger = get_logger("api")

TAGS = [
    {"name": "system", "description": "Liveness/readiness"},
    {"name": "users", "description": "Canonical user API (mode-aware)"},
    {"name": "posts", "description": "Canonical post/comment API (mode-aware)"},
    {"name": "feed", "description": "Social news feed"},
    {"name": "sharding", "description": "Shard admin and sharded data endpoints (Member 4)"},
    {"name": "demo", "description": "Educational/demo-only endpoints"},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")

    # Pin the composition root used by dependencies/tests to these settings.
    import api.dependencies as _deps

    _deps._platform = Platform(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        platform = get_platform()
        await platform.start()
        if platform.sharding_available and platform.health_checker is not None:
            probes = await platform.health_checker.check_all()
            down = [p.shard_id for p in probes if not p.reachable]
            if down:
                logger.warning("startup_shards_unavailable", shards=down)
        try:
            yield
        finally:
            await platform.shutdown()

    app = FastAPI(
        title="MetaScale",
        version="0.1.0",
        description=(
            "Educational distributed social-media database. "
            "Canonical normalized PostgreSQL + horizontal sharding (Member 4). "
            "Meta-inspired; not affiliated with Meta/Facebook/Instagram."
        ),
        openapi_tags=TAGS,
        lifespan=lifespan,
    )

    # ----------------------------------------------------- request id / logs
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    # ----------------------------------------------------- error envelopes
    @app.exception_handler(MetaScaleError)
    async def structured_error_handler(request: Request, exc: MetaScaleError) -> JSONResponse:
        if exc.http_status >= 500:
            logger.error(
                "request_failed",
                path=request.url.path,
                error=exc.error_code,
                detail=exc.message,
            )
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak stack traces / SQL / credentials to clients.
        logger.error("unhandled_exception", path=request.url.path, error=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": "INTERNAL_ERROR", "message": "Internal server error"},
        )

    # -------------------------------------------------------------- routes
    from api.routes.health import router as health_router
    from api.routes.feed import router as feed_router
    from api.routes.posts import router as posts_router
    from api.routes.shards import router as shards_router
    from api.routes.users import router as users_router

    app.include_router(health_router)
    app.include_router(users_router, prefix=settings.api_v1_prefix)
    app.include_router(posts_router, prefix=settings.api_v1_prefix)
    app.include_router(feed_router, prefix=settings.api_v1_prefix)
    app.include_router(shards_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "MetaScale",
            "mode": settings.mode.value,
            "docs": "/docs",
            "openapi": "/openapi.json",
        }

    return app


app = create_app()