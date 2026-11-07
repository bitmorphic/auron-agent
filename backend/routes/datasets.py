"""
AURON Datasets Router
Handles CSV upload, validation, previewing, and sample dataset selection.
Session-scoped: all dataset records are owned by the requesting session.
Sample loading is idempotent within a session (no duplicate records).
Source of Truth: PRD v3.0 §6 (FR-01, FR-06) & §14.
"""

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from backend.config import settings
from backend.csv_tools import CSVValidationError, save_uploaded_csv, validate_and_parse_csv
from backend.database import (
    create_dataset,
    get_dataset,
    get_dataset_by_name_and_session,
    list_datasets,
)
from backend.rate_limit import check_rate_limit
from backend.session import get_current_session_id

router = APIRouter(prefix="/api", tags=["Datasets"])


class SampleDatasetRequest(BaseModel):
    sample_name: str


def _clean_dataset_response(d: dict) -> dict:
    """Strip internal host filesystem paths before sending to client."""
    safe = dict(d)
    safe.pop("storage_path", None)
    safe.pop("owner_session_id", None)
    return safe


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
@router.post("/datasets/upload", status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Depends(get_current_session_id),
) -> dict:
    """
    Upload and validate a new CSV dataset.
    Enforces 10 MB size limit, 100K row limit, 200 column limit,
    and returns schema and 20-row preview. Scoped to current session.
    """
    await check_rate_limit(session_id, "upload", max_requests=10, window_seconds=60)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {e}")

    try:
        parsed = validate_and_parse_csv(
            data=content,
            filename=file.filename,
            max_size_bytes=settings.max_upload_size_bytes,
            max_rows=settings.max_rows,
            max_cols=settings.max_cols,
        )
    except CSVValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    dataset_id = str(uuid.uuid4())
    storage_path = save_uploaded_csv(content, dataset_id, settings.uploads_dir)

    record = create_dataset(
        filename=parsed["filename"],
        storage_path=str(storage_path),
        file_hash=parsed["file_hash"],
        file_size_bytes=parsed["file_size_bytes"],
        row_count=parsed["row_count"],
        column_count=parsed["column_count"],
        encoding=parsed["encoding"],
        schema=parsed["schema"],
        preview=parsed["preview"],
        is_sample=False,
        dataset_id=dataset_id,
        owner_session_id=session_id,
    )

    return _clean_dataset_response(record)


@router.get("/datasets")
async def get_datasets(
    session_id: str = Depends(get_current_session_id),
) -> list[dict]:
    """List datasets for the current session only."""
    datasets = list_datasets(owner_session_id=session_id)
    return [_clean_dataset_response(d) for d in datasets]


@router.get("/datasets/{dataset_id}")
async def get_single_dataset(
    dataset_id: str,
    session_id: str = Depends(get_current_session_id),
) -> dict:
    """Get metadata, schema, and preview for a dataset owned by the current session."""
    record = get_dataset(dataset_id, owner_session_id=session_id)
    if not record:
        # Return 404 to avoid resource enumeration
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return _clean_dataset_response(record)


@router.get("/samples")
async def list_available_samples() -> list[dict]:
    """List bundled sample datasets available for instant analysis. Public endpoint."""
    samples = []
    if settings.samples_dir.exists():
        for f in settings.samples_dir.glob("*.csv"):
            samples.append({
                "sample_name": f.stem,
                "filename": f.name,
                "size_bytes": f.stat().st_size,
            })
    return samples


@router.post("/datasets/sample", status_code=status.HTTP_201_CREATED)
async def load_sample_dataset(
    req: SampleDatasetRequest,
    session_id: str = Depends(get_current_session_id),
) -> dict:
    """
    Load a bundled sample dataset into the database for the current session.
    Idempotent: returns the existing record if already loaded for this session.
    """
    await check_rate_limit(session_id, "sample", max_requests=20, window_seconds=60)

    safe_name = Path(req.sample_name).name
    sample_file = settings.samples_dir / f"{safe_name}.csv"
    if not sample_file.is_file():
        # Check if requested with extension
        sample_file = settings.samples_dir / safe_name
        if not sample_file.is_file():
            raise HTTPException(status_code=404, detail=f"Sample dataset '{req.sample_name}' not found.")

    # Idempotency: return existing record if already loaded for this session
    existing = get_dataset_by_name_and_session(
        filename=sample_file.name,
        owner_session_id=session_id,
        is_sample=True,
    )
    if existing:
        return _clean_dataset_response(existing)

    content = sample_file.read_bytes()
    try:
        parsed = validate_and_parse_csv(
            data=content,
            filename=sample_file.name,
            max_size_bytes=settings.max_upload_size_bytes,
        )
    except CSVValidationError as e:
        raise HTTPException(status_code=500, detail=f"Sample dataset validation error: {e}")

    dataset_id = str(uuid.uuid4())
    storage_path = save_uploaded_csv(content, dataset_id, settings.uploads_dir)

    record = create_dataset(
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
        dataset_id=dataset_id,
        owner_session_id=session_id,
    )

    return _clean_dataset_response(record)
