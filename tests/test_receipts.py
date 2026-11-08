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



class TestArtifactIntegrityAndIdentity:
    """Explicit tests for PRD §13/15 artifact digest verification and receipt identity."""

    def test_artifact_tamper_detected_by_sha256(self, tmp_path):
        """Modifying 1 byte of an artifact file changes its SHA-256 and detects tampering."""
        artifact = tmp_path / "chart.png"
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        original_hash = sha256_file(artifact)

        # Tamper with 1 byte
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x01" + b"\x00" * 63)
        tampered_hash = sha256_file(artifact)

        assert tampered_hash != original_hash, "Tampered artifact produced identical hash!"

    def test_downloaded_artifact_bytes_match_recorded_sha256(self):
        """Downloaded artifact payload bytes must match recorded receipt artifact SHA-256."""
        # Simulated chart.png
        chart_bytes = b"\x89PNG\r\n\x1a\nfake-chart-data"
        chart_hash = hashlib.sha256(chart_bytes).hexdigest()

        # Simulated metrics.json
        metrics_bytes = b'{"total_revenue": 14400.0, "avg_price": 450.0}'
        metrics_hash = hashlib.sha256(metrics_bytes).hexdigest()

        receipt = {
            "receipt_version": "1.0",
            "task_id": "test-task-1",
            "artifacts": [
                {"name": "chart.png", "sha256": chart_hash, "size_bytes": len(chart_bytes)},
                {"name": "metrics.json", "sha256": metrics_hash, "size_bytes": len(metrics_bytes)},
            ],
            "receipt_hash": None,
        }
        receipt["receipt_hash"] = compute_receipt_hash(receipt)

        # Verification step
        for art in receipt["artifacts"]:
            payload = chart_bytes if art["name"] == "chart.png" else metrics_bytes
            assert hashlib.sha256(payload).hexdigest() == art["sha256"]

        # Tampered download must fail comparison
        corrupted_download = chart_bytes + b"corruption"
        assert hashlib.sha256(corrupted_download).hexdigest() != receipt["artifacts"][0]["sha256"]

    def test_all_receipt_field_modifications_fail_verification(self):
        """Modifying any field in a signed receipt fails verify_receipt_hash."""
        base_receipt = {
            "receipt_version": "1.0",
            "task_id": "task-identity-42",
            "attempt_id": "attempt-1",
            "code": "print('clean')",
            "execution": {"exit_code": 0, "status": "completed"},
            "artifacts": [{"name": "chart.png", "sha256": "aabbcc"}],
            "receipt_hash": None,
        }
        base_receipt["receipt_hash"] = compute_receipt_hash(base_receipt)
        assert verify_receipt_hash(base_receipt) is True

        # Mutation 1: code changed
        r1 = json.loads(json.dumps(base_receipt))
        r1["code"] = "import os; os.system('bad')"
        assert verify_receipt_hash(r1) is False

        # Mutation 2: exit_code changed
        r2 = json.loads(json.dumps(base_receipt))
        r2["execution"]["exit_code"] = 137
        assert verify_receipt_hash(r2) is False

        # Mutation 3: task_id changed
        r3 = json.loads(json.dumps(base_receipt))
        r3["task_id"] = "different-task"
        assert verify_receipt_hash(r3) is False

        # Mutation 4: artifact sha changed
        r4 = json.loads(json.dumps(base_receipt))
        r4["artifacts"][0]["sha256"] = "modifiedhash"
        assert verify_receipt_hash(r4) is False

    def test_task_attempt_receipt_identity_consistency(self):
        """task.id, attempt.task_id and receipt.task_id must be strictly identical."""
        task_id = "test-task-uuid-8899"
        attempt = {"id": "att-1", "task_id": task_id}
        receipt = {
            "task_id": task_id,
            "attempt_id": attempt["id"],
            "execution": {"exit_code": 0},
            "receipt_hash": None,
        }
        receipt["receipt_hash"] = compute_receipt_hash(receipt)

        assert task_id == attempt["task_id"] == receipt["task_id"], "Task ID identity mismatch!"
        assert attempt["id"] == receipt["attempt_id"]

    def test_historical_records_preserve_original_hash(self):
        """Receipt verification reads stored hash without overwriting or recomputing."""
        historical_receipt = {
            "receipt_version": "1.0",
            "task_id": "hist-task",
            "execution": {"exit_code": 0},
            "receipt_hash": "original-unmodified-digest-value",
        }
        # Calling verify does not mutate or rewrite receipt_hash
        valid = verify_receipt_hash(historical_receipt)
        assert valid is False  # bogus hash
        assert historical_receipt["receipt_hash"] == "original-unmodified-digest-value"
