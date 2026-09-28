"""MetaScale FastAPI application factory.

Run locally:  uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from common.config import Settings, get_settings
from common.exceptions import MetaScaleError
from common.logging import configure_logging, get_logger, request_id_var
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.dependencies import Platform, get_platform
from services.observability.metrics import REGISTRY

logger = get_logger("api")

TAGS = [
    {"name": "system", "description": "Liveness/readiness"},
    {"name": "users", "description": "Canonical user API (mode-aware)"},
    {"name": "posts", "description": "Canonical post/comment API (mode-aware)"},
    {"name": "comments", "description": "Comments as a first-class resource (read/delete)"},
    {"name": "likes", "description": "Likes as a first-class resource (read/delete)"},
    {"name": "follows", "description": "Follow edges as a first-class resource (read/delete)"},
    {"name": "sharding", "description": "Shard admin and sharded data endpoints (Member 4)"},
    {"name": "replication", "description": "Primary/replica routing + failover (Member 5)"},
    {"name": "cache", "description": "Redis cache-aside status and metrics (Member 6)"},
    {"name": "feed", "description": "Feed generation over canonical relationships (Member 7)"},
    {"name": "media", "description": "Media metadata + blob storage (Member 8)"},
    {"name": "search", "description": "Derived search index and autocomplete (Member 10)"},
    {"name": "database", "description": "Live schema, rows and change stream (DB Explorer)"},
    {"name": "events", "description": "Domain event bus: transports, types, recent events"},
    {"name": "benchmarks", "description": "Measurements taken live against the running system"},
    {"name": "observability", "description": "Prometheus metrics and measured latency (Member 11)"},
    {"name": "demo", "description": "Educational/demo-only endpoints (clearly labelled)"},
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
            "Educational distributed social-media database. Canonical "
            "normalized PostgreSQL is the source of truth; sharding (M4), "
            "replication (M5), Redis cache-aside (M6), denormalized read "
            "model (M3), feed (M7), media (M8), "
            "search (M10) and observability (M11) are composed around it. "
            "Meta-inspired; not affiliated with Meta/Facebook/Instagram."
        ),
        openapi_tags=TAGS,
        lifespan=lifespan,
    )

    # ----------------------------------------------------- request id / logs
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:
        """Request ids + real request/latency metrics (Member 11)."""
        import time

        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            REGISTRY.increment("request_count")
            REGISTRY.observe("request_latency_ms", (time.perf_counter() - start) * 1000.0)
            if response.status_code >= 500:
                REGISTRY.increment("errors_total")
        except Exception:
            REGISTRY.increment("errors_total")
            raise
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
    from api.routes.benchmarks import router as benchmarks_router
    from api.routes.cache import router as cache_router
    from api.routes.database import router as database_router
    from api.routes.events import router as events_router
    from api.routes.feed import router as feed_router
    from api.routes.health import router as health_router
    from api.routes.health import v1_router as health_v1_router
    from api.routes.interactions import (
        comments_router,
        follows_router,
        likes_router,
    )
    from api.routes.media import router as media_router
    from api.routes.metrics import prometheus_router
    from api.routes.metrics import router as metrics_router
    from api.routes.observability import router as observability_router
    from api.routes.posts import router as posts_router
    from api.routes.read_model import router as read_model_router
    from api.routes.replication import router as replication_router
    from api.routes.search import router as search_router
    from api.routes.shards import router as shards_router
    from api.routes.users import router as users_router

    # /metrics (Prometheus scrape) and /health, /ready are unversioned.
    app.include_router(prometheus_router)
    app.include_router(metrics_router, prefix=settings.api_v1_prefix)
    app.include_router(health_router)
    app.include_router(users_router, prefix=settings.api_v1_prefix)
    app.include_router(posts_router, prefix=settings.api_v1_prefix)
    # Resource-oriented aliases required by the integration contract.
    app.include_router(comments_router, prefix=settings.api_v1_prefix)
    app.include_router(likes_router, prefix=settings.api_v1_prefix)
    app.include_router(follows_router, prefix=settings.api_v1_prefix)
    app.include_router(feed_router, prefix=settings.api_v1_prefix)
    app.include_router(read_model_router, prefix=settings.api_v1_prefix)
    app.include_router(media_router, prefix=settings.api_v1_prefix)
    app.include_router(search_router, prefix=settings.api_v1_prefix)
    app.include_router(cache_router, prefix=settings.api_v1_prefix)
    app.include_router(shards_router, prefix=settings.api_v1_prefix)
    app.include_router(replication_router, prefix=settings.api_v1_prefix)
    # Integrated-system routes: database explorer + change stream, event bus
    # introspection, observability surface, live benchmarks.
    app.include_router(database_router, prefix=settings.api_v1_prefix)
    app.include_router(events_router, prefix=settings.api_v1_prefix)
    app.include_router(observability_router, prefix=settings.api_v1_prefix)
    app.include_router(benchmarks_router, prefix=settings.api_v1_prefix)
    # GET /api/v1/health — aggregate dependency health (healthy|degraded|unavailable)
    app.include_router(health_v1_router)

    # ------------------------------------------------- built frontend (optional)
    # When `npm run build` has produced frontend/dist, the Architecture Control
    # Center is served from this same origin — no CORS, no second port. The API
    # routes declared above always win, so mounting last is safe.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (dist / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        for route in ("/dashboard", "/observability", "/benchmarks"):
            app.mount(
                route,
                StaticFiles(directory=str(dist), html=True),
                name=f"ui{route.replace('/', '-')}",
            )
        # The bundle is referenced with absolute paths from every page, so the
        # asset directory needs its own mount.
        app.mount(
            "/assets",
            StaticFiles(directory=str(dist / "assets")),
            name="ui-assets",
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": "MetaScale",
            "mode": settings.mode.value,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "metrics": "/metrics",
            "health": "/health",
            "ready": "/ready",
            "api_health": f"{settings.api_v1_prefix}/health",
            "dashboard": "/dashboard",
        }

    return app


app = create_app()
