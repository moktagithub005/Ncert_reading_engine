from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint

from app.api.admin.auth import router as auth_router
from app.api.admin.ingestion import router as ingestion_router
from app.api.admin.publish import router as publish_router
from app.api.admin.upload import router as upload_router
from app.api.public.content import router as public_router
from app.cache.redis import get_redis
from app.config import get_settings
from app.core.logging import configure_logging
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.db.session import engine

configure_logging()
settings = get_settings()
if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(dsn=settings.sentry_dsn)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    get_redis().close()
    engine.dispose()


app = FastAPI(title="NCERT Content Platform", version="1.0.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/test-ui")


@app.get("/test-ui", include_in_schema=False)
def test_ui() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "test_ui.html")


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-None-Match"],
)
app.include_router(auth_router, prefix="/api/v1/admin")
app.include_router(upload_router, prefix="/api/v1/admin")
app.include_router(ingestion_router, prefix="/api/v1/admin")
app.include_router(publish_router, prefix="/api/v1/admin")
app.include_router(public_router, prefix="/api/v1")


@app.middleware("http")
async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    structlog.contextvars.bind_contextvars(request_id=request_id)
    start = perf_counter()
    try:
        response: Response
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                response = JSONResponse(
                    status_code=400, content={"detail": "Invalid content length"}
                )
            else:
                if declared_size > settings.body_size_limit_bytes:
                    response = JSONResponse(
                        status_code=413, content={"detail": "Request too large"}
                    )
                else:
                    response = await call_next(request)
        else:
            response = await call_next(request)
        elapsed = perf_counter() - start
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, path).observe(elapsed)
        return response
    finally:
        structlog.contextvars.clear_contextvars()


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
def ready() -> JSONResponse:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        get_redis().ping()
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": str(exc)})
    return JSONResponse(content={"status": "ready"})


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/v1/health", include_in_schema=False)
def api_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/ready", include_in_schema=False)
def api_ready() -> JSONResponse:
    return ready()
