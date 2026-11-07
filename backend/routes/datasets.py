"""
AURON Datasets Router
Handles CSV upload, validation, previewing, and sample dataset selection.
Source of Truth: PRD v3.0 §6 (FR-01, FR-06) & §14.
"""

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from backend.config import settings
from backend.csv_tools import CSVValidationError, save_uploaded_csv, validate_and_parse_csv
from backend.database import create_dataset, get_dataset, list_datasets

router = APIRouter(prefix="/api", tags=["Datasets"])


class SampleDatasetRequest(BaseModel):
    sample_name: str


def _clean_dataset_response(d: dict) -> dict:
    """Strip internal host filesystem paths before sending to client."""
    safe = dict(d)
    safe.pop("storage_path", None)
    return safe


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
@router.post("/datasets/upload", status_code=status.HTTP_201_CREATED)
async def upload_dataset(file: UploadFile = File(...)) -> dict:
    """
    Upload and validate a new CSV dataset.
    Enforces 10 MB size limit, 100K row limit, 200 column limit,
    and returns schema and 20-row preview.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename.")

    # Read uploaded content with size bound
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
    )

    return _clean_dataset_response(record)


@router.get("/datasets")
async def get_datasets() -> list[dict]:
    """List all registered datasets."""
    datasets = list_datasets()
    return [_clean_dataset_response(d) for d in datasets]


@router.get("/datasets/{dataset_id}")
async def get_single_dataset(dataset_id: str) -> dict:
    """Get metadata, schema, and preview for a single dataset."""
    record = get_dataset(dataset_id)
    if not record:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return _clean_dataset_response(record)


@router.get("/samples")
async def list_available_samples() -> list[dict]:
    """List bundled sample datasets available for instant analysis."""
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
async def load_sample_dataset(req: SampleDatasetRequest) -> dict:
    """Load a bundled sample dataset into the database."""
    safe_name = Path(req.sample_name).name
    sample_file = settings.samples_dir / f"{safe_name}.csv"
    if not sample_file.is_file():
        # Check if requested with extension
        sample_file = settings.samples_dir / safe_name
        if not sample_file.is_file():
            raise HTTPException(status_code=404, detail=f"Sample dataset '{req.sample_name}' not found.")

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
    )

    return _clean_dataset_response(record)

