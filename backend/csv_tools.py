"""
AURON CSV Ingestion and Validation Layer
Enforces PRD §6 FR-01:
- Size limit (10 MB)
- Row limit (100,000)
- Column limit (200)
- Encoding fallback (UTF-8 -> Latin-1)
- 20-row capped preview
- Schema & quality indicators
- SHA-256 hash
- Untrusted data sanitization
"""

import hashlib
import io
import uuid
from pathlib import Path
from typing import Any, Tuple

import pandas as pd
from backend.config import settings


class CSVValidationError(ValueError):
    """Raised when CSV input violates safety or size policies."""
    pass


def compute_sha256(data: bytes) -> str:
    """Compute hex SHA-256 digest of raw byte content."""
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def detect_and_decode(data: bytes) -> Tuple[str, str]:
    """
    Attempt UTF-8 decoding; fallback to latin-1 per PRD §6 FR-01.
    Returns (decoded_text, encoding_name).
    """
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1"), "latin-1"
        except Exception as e:
            raise CSVValidationError(f"Failed to decode CSV data: {e}")


def infer_column_type(series: pd.Series) -> str:
    """Infer high-level data type for schema reporting."""
    if pd.api.types.is_numeric_dtype(series):
        if pd.api.types.is_integer_dtype(series):
            return "integer"
        return "float"
    elif pd.api.types.is_bool_dtype(series):
        return "boolean"
    elif pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    else:
        # Check if text looks like datetime
        if series.dropna().empty:
            return "empty"
        return "string"


def validate_and_parse_csv(
    data: bytes,
    filename: str,
    max_size_bytes: int = settings.max_upload_size_bytes,
    max_rows: int = settings.max_rows,
    max_cols: int = settings.max_cols,
) -> dict[str, Any]:
    """
    Validate, parse, and generate schema & preview for CSV data.
    Enforces all strict limits before saving.
    """
    # 1. Size check
    if len(data) == 0:
        raise CSVValidationError("Uploaded file is empty.")
    if len(data) > max_size_bytes:
        raise CSVValidationError(
            f"File size ({len(data)} bytes) exceeds maximum allowed limit ({max_size_bytes} bytes)."
        )

    # 2. SHA-256 computation
    file_hash = compute_sha256(data)

    # 3. Decode text
    text, encoding = detect_and_decode(data)

    # 4. Parse CSV with pandas
    try:
        # Read header and first chunk to inspect column count safely
        df = pd.read_csv(io.StringIO(text))
    except Exception as e:
        raise CSVValidationError(f"Invalid CSV structure: {str(e)}")

    row_count = len(df)
    col_count = len(df.columns)

    if col_count > max_cols:
        raise CSVValidationError(
            f"Column count ({col_count}) exceeds limit ({max_cols})."
        )
    if row_count > max_rows:
        raise CSVValidationError(
            f"Row count ({row_count}) exceeds limit ({max_rows})."
        )
    if col_count == 0:
        raise CSVValidationError("CSV contains no columns.")

    # 5. Extract schema and quality indicators
    schema: dict[str, Any] = {
        "columns": [],
        "quality": {
            "total_rows": row_count,
            "total_columns": col_count,
            "duplicate_rows": int(df.duplicated().sum()),
            "empty_columns": int(df.isna().all().sum()),
        }
    }

    for col in df.columns:
        col_series = df[col]
        dtype_inferred = infer_column_type(col_series)
        null_count = int(col_series.isna().sum())
        schema["columns"].append({
            "name": str(col)[:100],  # Bound column name length
            "dtype": dtype_inferred,
            "null_count": null_count,
            "null_pct": round((null_count / max(1, row_count)) * 100, 2),
        })

    # 6. Extract capped preview (first 20 rows, capped string lengths)
    preview_rows: list[dict[str, Any]] = []
    preview_df = df.head(20)

    for _, row in preview_df.iterrows():
        row_dict: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                row_dict[str(col)] = None
            elif isinstance(val, (int, float, bool)):
                row_dict[str(col)] = val
            else:
                # Cap string cells to 100 characters to prevent prompt bloat / injection
                row_dict[str(col)] = str(val)[:100]
        preview_rows.append(row_dict)

    return {
        "filename": filename,
        "file_hash": file_hash,
        "file_size_bytes": len(data),
        "row_count": row_count,
        "column_count": col_count,
        "encoding": encoding,
        "schema": schema,
        "preview": preview_rows,
    }


def save_uploaded_csv(data: bytes, file_id: str, dest_dir: Path = settings.uploads_dir) -> Path:
    """Save original raw CSV to host storage outside sandbox."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_path = dest_dir / f"{file_id}.csv"
    target_path.write_bytes(data)
    return target_path

