"""
AURON Backend Application Entrypoint
FastAPI application with lifespan lifecycle, CORS, routes, and error handling.
Source of Truth: PRD v3.0 §10 & §14.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import init_db, list_datasets, create_dataset
from backend.csv_tools import validate_and_parse_csv, save_uploaded_csv
from backend.routes import health, datasets, tasks
from sandbox.runner import SandboxRunner, reap_orphaned_containers

# Configure logging
logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("auron")


def seed_sample_datasets():
    """Seed bundled sample CSVs into database on first startup."""
    existing = list_datasets()
    existing_filenames = {d["filename"] for d in existing}

    if not settings.samples_dir.exists():
        return

    for csv_file in settings.samples_dir.glob("*.csv"):
        if csv_file.name in existing_filenames:
            continue
        try:
            content = csv_file.read_bytes()
            parsed = validate_and_parse_csv(
                data=content,
                filename=csv_file.name,
                max_size_bytes=settings.max_upload_size_bytes,
            )
            import uuid
            d_id = str(uuid.uuid4())
            storage_path = save_uploaded_csv(content, d_id, settings.uploads_dir)
            create_dataset(
                filename=parsed["filename"],
                storage_path=str(storage_path),
                file_hash=parsed["file_hash"],
                file_size_bytes=parsed["file_size_bytes"],
                row_count=parsed["row_count"],
                column_count=parsed["column_count"],
                encoding=parsed["encoding"],
                schema=parsed["schema"],
                preview=parsed["preview"],
                is_sample=True,
                dataset_id=d_id,
            )
            logger.info(f"Seeded sample dataset: {csv_file.name}")
        except Exception as e:
            logger.warning(f"Could not seed sample {csv_file.name}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Initializing AURON backend...")
    settings.ensure_directories()
    init_db()
    seed_sample_datasets()

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

# CORS configuration for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production per PRD §14
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global safe exception handler
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
