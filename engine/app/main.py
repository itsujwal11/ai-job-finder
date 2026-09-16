"""FastAPI application: n8n pipeline API, dashboard API and the built dashboard."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import pipeline_routes, ui_routes
from .api.auth import dashboard_auth_middleware
from .config import get_env
from .events import log_event

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("app")
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_pool(get_env().database_url)
    applied = db.run_migrations()
    if applied:
        log.info("Applied migrations: %s", ", ".join(applied))
    yield
    db.close_pool()


app = FastAPI(title="AI Job Discovery Engine", version="1.0.0", lifespan=lifespan)
app.middleware("http")(dashboard_auth_middleware)
app.include_router(pipeline_routes.router)
app.include_router(ui_routes.router)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    log_event("engine_error", f"{request.method} {request.url.path}: {type(exc).__name__}: {exc}", level="error")
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


@app.get("/api/health")
def health() -> JSONResponse:
    try:
        db.fetch_value("SELECT 1 AS ok")
        database = True
    except Exception:
        database = False
    return JSONResponse({"status": "ok" if database else "degraded", "database": database}, status_code=200 if database else 503)


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")
else:
    @app.get("/", response_class=HTMLResponse)
    def dashboard_missing() -> str:
        return "<h1>Dashboard not built</h1><p>Run <code>npm run build</code> in dashboard/ or use Docker.</p>"
