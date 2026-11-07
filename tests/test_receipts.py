"""Unit tests for receipt generation and SHA-256 hashing.

These tests run without Docker — they verify the receipt logic in isolation.
"""

import hashlib
import json

import pytest

from sandbox.runner import (
    compute_receipt_hash,
    sha256_bytes,
    sha256_file,
    verify_receipt_hash,
)


class TestReceiptHash:
    """Test receipt hash computation and verification."""

    def test_hash_is_valid_sha256(self):
        """Receipt hash must be a 64-character hex SHA-256 digest."""
        receipt = {
            "receipt_version": "1.0",
            "task_id": "test-task",
            "execution": {"exit_code": 0, "status": "completed"},
            "receipt_hash": None,
        }
        h = compute_receipt_hash(receipt)
        assert isinstance(h, str)
        assert len(h) == 64
        int(h, 16)  # must be valid hex

    def test_hash_excludes_hash_field(self):
        """receipt_hash field must not affect its own computation."""
        receipt = {
            "task_id": "abc",
            "execution": {"exit_code": 0},
            "receipt_hash": None,
        }
        h1 = compute_receipt_hash(receipt)

        receipt["receipt_hash"] = "something-else"
        h2 = compute_receipt_hash(receipt)

        assert h1 == h2

    def test_hash_deterministic(self):
        """Same receipt data must always produce the same hash."""
        receipt = {
            "receipt_version": "1.0",
            "task_id": "determinism-test",
            "data": [1, 2, 3],
            "receipt_hash": None,
        }
        assert compute_receipt_hash(receipt) == compute_receipt_hash(receipt)

    def test_hash_changes_with_data(self):
        """Different receipt data must produce different hashes."""
        r1 = {"task_id": "task-1", "receipt_hash": None}
        r2 = {"task_id": "task-2", "receipt_hash": None}
        assert compute_receipt_hash(r1) != compute_receipt_hash(r2)

    def test_verify_valid_receipt(self):
        """verify_receipt_hash returns True for untampered receipt."""
        receipt = {
            "receipt_version": "1.0",
            "task_id": "verify-test",
            "execution": {"exit_code": 0},
            "receipt_hash": None,
        }
        receipt["receipt_hash"] = compute_receipt_hash(receipt)
        assert verify_receipt_hash(receipt) is True

    def test_verify_detects_tampering(self):
        """verify_receipt_hash returns False when data is modified."""
        receipt = {
            "receipt_version": "1.0",
            "task_id": "tamper-test",
            "execution": {"exit_code": 0},
            "receipt_hash": None,
        }
        receipt["receipt_hash"] = compute_receipt_hash(receipt)

        # Tamper
        receipt["execution"]["exit_code"] = 1

        assert verify_receipt_hash(receipt) is False

    def test_verify_missing_hash(self):
        """Receipt with no hash field fails verification."""
        assert verify_receipt_hash({"task_id": "no-hash"}) is False

    def test_verify_empty_hash(self):
        """Receipt with empty hash string fails verification."""
        assert verify_receipt_hash({"task_id": "x", "receipt_hash": ""}) is False


class TestSHA256Utilities:
    """Test SHA-256 helper functions."""

    def test_sha256_file(self, tmp_path):
        """File hash must match hashlib reference."""
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert sha256_file(f) == expected

    def test_sha256_file_binary(self, tmp_path):
        """Binary file hash must be correct."""
        data = bytes(range(256))
        f = tmp_path / "binary.bin"
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_file(f) == expected

    def test_sha256_bytes(self):
        """Bytes hash must match hashlib reference."""
        data = b"test data"
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_bytes(data) == expected

    def test_sha256_empty(self):
        """Empty input must produce the well-known SHA-256 empty hash."""
        empty_hash = hashlib.sha256(b"").hexdigest()
        assert sha256_bytes(b"") == empty_hash

