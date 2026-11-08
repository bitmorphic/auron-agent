"""
AURON Backend Application Entrypoint
FastAPI application with lifespan lifecycle, session middleware, CORS, routes, and error handling.
Source of Truth: PRD v3.0 §10 & §14.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import init_db, list_datasets, create_dataset, get_session
from backend.csv_tools import validate_and_parse_csv, save_uploaded_csv
from backend.routes import health, datasets, tasks, redteam
from backend.session import (
    COOKIE_NAME,
    get_or_create_session,
    get_session_from_request,
    set_session_cookie,
)
from sandbox.runner import SandboxRunner, reap_orphaned_containers

# Configure logging
logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("auron")


def seed_sample_datasets():
    """Seed bundled sample CSVs into database on first startup (legacy/global seed only)."""
    # On fresh instances, seed the samples with the LEGACY_SESSION_ID so they
    # are not accidentally leaked into user sessions. Users load samples via the
    # /api/datasets/sample endpoint which creates per-session records.
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Initializing AURON backend...")
    settings.ensure_directories()
    init_db()

    # Reap orphaned AURON containers
    try:
        reaped = reap_orphaned_containers()
        if reaped:
            logger.info(f"Reaped {len(reaped)} orphaned sandbox containers on startup.")
    except Exception as e:
        logger.warning(f"Orphan reaper check on startup failed: {e}")

    logger.info("AURON backend initialized successfully.")
    yield
    logger.info("Shutting down AURON backend...")


app = FastAPI(
    title="AURON API",
    description="Autonomous Analysis. Verified Execution.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS configuration — same-origin enforcement via session cookies.
# allow_credentials must be True for cookies to be sent cross-origin in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://45.77.19.39.sslip.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://localhost",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-RedTeam-Token", "Authorization"],
)


# ─── Session Bootstrap Middleware ─────────────────────────────────────────────


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    """
    Automatically bootstrap an anonymous session on every request.
    - If the request has a valid session cookie, extracts the session_id into request.state.
    - If not, mints a fresh anonymous session and sets the cookie on the response.
    - Never exposes the raw token in response bodies or logs.
    """
    existing = get_session_from_request(request)
    new_token: Optional[str] = None

    if existing:
        request.state.session_id = existing["id"]
        request.state.is_new_session = False
    else:
        session_id, new_token = get_or_create_session(request)
        request.state.session_id = session_id
        request.state.is_new_session = True

    response = await call_next(request)

    if new_token:
        set_session_cookie(response, new_token, request=request)

    return response


# ─── Session Bootstrap Endpoint ───────────────────────────────────────────────


@app.post("/api/session", include_in_schema=True, tags=["Session"])
async def bootstrap_session(request: Request, response: Response):
    """
    Explicitly bootstrap or renew an anonymous workspace session.
    Returns the session creation time and expiry. Never returns the token.
    The token is set as a Secure HttpOnly cookie.
    """
    session_id = getattr(request.state, "session_id", None)
    is_new = getattr(request.state, "is_new_session", False)
    session = get_session(session_id) if session_id else None

    if session:
        return {
            "bootstrapped": is_new,
            "created_at": session.get("created_at"),
            "expires_at": session.get("expires_at"),
        }

    return {
        "bootstrapped": False,
        "created_at": None,
        "expires_at": None,
    }


# ─── Global safe exception handler ───────────────────────────────────────────


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Execution details have been safely logged."},
    )


# Include API routes
app.include_router(health.router)
app.include_router(datasets.router)
app.include_router(tasks.router)
app.include_router(redteam.router)

# Mount frontend interface
frontend_html = Path("frontend/index.html")
if frontend_html.is_file():
    from fastapi.responses import FileResponse

    @app.get("/", include_in_schema=False)
    async def serve_frontend():
        return FileResponse(str(frontend_html))

frontend_dist = Path("frontend/dist")
if frontend_dist.is_dir():
    app.mount("/dist", StaticFiles(directory=str(frontend_dist), html=True), name="frontend_dist")
