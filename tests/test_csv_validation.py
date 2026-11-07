"""
Tests for CSV Ingestion and Validation (PRD §6 FR-01).
Tests upload limits, encoding fallback, schema inference, quality flags, and preview format.
"""

import io
import pytest
import pandas as pd
from backend.csv_tools import (
    CSVValidationError,
    compute_sha256,
    detect_and_decode,
    validate_and_parse_csv,
)


def test_sha256_computation():
    data = b"col1,col2\n1,2\n"
    expected = "f2d34a5d8928ddb37492c10b7b12d9370aeae9e7df1787c9dc19cf5354be3483"
    import hashlib
    h = hashlib.sha256(data).hexdigest()
    assert compute_sha256(data) == h


def test_utf8_decoding():
    data = "name,age\nAlice,30\n".encode("utf-8")
    text, enc = detect_and_decode(data)
    assert enc == "utf-8"
    assert "Alice" in text


def test_latin1_fallback():
    # Character 0xe9 is 'é' in Latin-1 but invalid alone in UTF-8
    data = b"name,city\nRen\xe9,Montr\xe9al\n"
    text, enc = detect_and_decode(data)
    assert enc == "latin-1"
    assert "René" in text


def test_empty_file_rejected():
    with pytest.raises(CSVValidationError, match="empty"):
        validate_and_parse_csv(b"", "empty.csv")


def test_oversized_file_rejected():
    data = b"a" * 100
    with pytest.raises(CSVValidationError, match="exceeds maximum allowed limit"):
        validate_and_parse_csv(data, "large.csv", max_size_bytes=50)


def test_max_columns_exceeded():
    cols = ",".join([f"c{i}" for i in range(10)])
    row = ",".join(["1"] * 10)
    data = f"{cols}\n{row}\n".encode("utf-8")
    with pytest.raises(CSVValidationError, match="Column count.*exceeds limit"):
        validate_and_parse_csv(data, "wide.csv", max_cols=5)


def test_max_rows_exceeded():
    rows = ["col1"] + [str(i) for i in range(20)]
    data = "\n".join(rows).encode("utf-8")
    with pytest.raises(CSVValidationError, match="Row count.*exceeds limit"):
        validate_and_parse_csv(data, "tall.csv", max_rows=10)


def test_valid_csv_parsing_and_preview():
    csv_text = (
        "id,product,price,active,date\n"
        "1,Widget,19.99,True,2026-01-01\n"
        "2,Gadget,29.50,False,2026-01-02\n"
        "3,Doohickey,,True,2026-01-03\n"
    )
    data = csv_text.encode("utf-8")
    parsed = validate_and_parse_csv(data, "sample.csv")

    assert parsed["filename"] == "sample.csv"
    assert parsed["row_count"] == 3
    assert parsed["column_count"] == 5
    assert parsed["encoding"] == "utf-8"

    # Schema checks
    schema = parsed["schema"]
    col_names = [c["name"] for c in schema["columns"]]
    assert col_names == ["id", "product", "price", "active", "date"]

    # Quality indicators
    quality = schema["quality"]
    assert quality["total_rows"] == 3
    assert quality["total_columns"] == 5
    assert quality["empty_columns"] == 0

    # Preview check
    preview = parsed["preview"]
    assert len(preview) == 3
    assert preview[0]["product"] == "Widget"
    assert preview[2]["price"] is None  # missing value represented as None


def test_cell_capping_in_preview():
    # Long cell should be capped at 100 characters in preview
    long_str = "A" * 200
    csv_text = f"col1,col2\n1,{long_str}\n"
    data = csv_text.encode("utf-8")
    parsed = validate_and_parse_csv(data, "long.csv")
    assert len(parsed["preview"][0]["col2"]) == 100

