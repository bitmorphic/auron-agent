"""Integration tests for the AURON sandbox runner.

Tests in this module require Docker to be running.  They exercise the
full container lifecycle: create → start → wait → logs → inspect → cp → rm.
"""

import json
import subprocess

import pytest

from sandbox.runner import DOCKER_CMD, SandboxPolicy, SandboxRunner, verify_receipt_hash
from tests.conftest import requires_docker


@requires_docker
class TestSuccessfulExecution:
    """Happy-path execution tests."""

    def test_basic_execution(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        """A correct script should complete with exit 0 and captured output."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, simple_script)

        assert receipt["execution"]["status"] == "completed"
        assert receipt["execution"]["exit_code"] == 0
        assert receipt["execution"]["timed_out"] is False
        assert receipt["execution"]["oom_killed"] is False
        assert receipt["timestamps"]["duration_ms"] > 0
        assert receipt["code"]  # exact source captured
        assert receipt["output"]["stdout"]  # script printed JSON

        # Parse the stdout to verify real execution
        result = json.loads(receipt["output"]["stdout"])
        assert result["rows"] == 5
        assert result["total_quantity"] == 425

    def test_receipt_integrity(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        """Receipt hash must be verifiable."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, simple_script)

        assert receipt["receipt_hash"] is not None
        assert len(receipt["receipt_hash"]) == 64
        assert verify_receipt_hash(receipt) is True

    def test_receipt_saved_to_disk(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        """Receipt JSON file must be written to the receipts directory."""
        rec_dir = tmp_path / "rec"
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=rec_dir,
        )
        receipt = runner.run(sample_csv, simple_script)

        task_id = receipt["task_id"]
        attempt_id = receipt["attempt_id"]
        receipt_path = rec_dir / f"{task_id}_{attempt_id}" / "receipt.json"
        assert receipt_path.exists()

        saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert saved["receipt_hash"] == receipt["receipt_hash"]

    def test_receipt_required_fields(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        """Receipt must contain all required evidence fields."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, simple_script)

        required = [
            "receipt_version", "task_id", "attempt_id", "container_name",
            "timestamps", "code", "input_hash", "execution", "output",
            "sandbox_policy", "container", "artifacts", "cleanup_verified",
            "receipt_hash",
        ]
        for field in required:
            assert field in receipt, f"Missing required field: {field}"

        # Sub-fields
        assert "started_at" in receipt["timestamps"]
        assert "ended_at" in receipt["timestamps"]
        assert "duration_ms" in receipt["timestamps"]
        assert "exit_code" in receipt["execution"]
        assert "stdout" in receipt["output"]
        assert "stderr" in receipt["output"]
        assert "inspect" in receipt["container"]
        assert "image_tag" in receipt["container"]
        assert "image_digest" in receipt["container"]

    def test_input_hash_captured(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        """Input CSV SHA-256 hash must be recorded in receipt."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, simple_script)
        assert receipt["input_hash"]
        assert len(receipt["input_hash"]) == 64


@requires_docker
class TestFailureHandling:
    """Tests for non-zero exit, timeout, and error conditions."""

    def test_nonzero_exit(
        self, sandbox_image, sample_csv, failing_script, tmp_path,
    ):
        """Non-zero exit must be captured with 'failed' status."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, failing_script)

        assert receipt["execution"]["status"] == "failed"
        assert receipt["execution"]["exit_code"] != 0
        assert "About to fail" in receipt["output"]["stderr"]
        assert receipt["cleanup_verified"] is True

    @pytest.mark.slow
    def test_timeout_handling(
        self, sandbox_image, sample_csv, timeout_script, tmp_path,
    ):
        """Timeout must be enforced by the controller, not the script."""
        policy = SandboxPolicy(timeout_seconds=5)
        runner = SandboxRunner(
            policy=policy,
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, timeout_script)

        assert receipt["execution"]["timed_out"] is True
        assert receipt["execution"]["status"] == "timeout"
        assert receipt["cleanup_verified"] is True
        # Duration should be approximately the timeout
        assert receipt["timestamps"]["duration_ms"] >= 4000


@requires_docker
class TestContainerCleanup:
    """Tests for guaranteed container removal."""

    def test_cleanup_after_success(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        """Container must not exist after successful execution."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, simple_script)

        assert receipt["cleanup_verified"] is True
        result = subprocess.run(
            [DOCKER_CMD, "inspect", receipt["container_name"]],
            capture_output=True,
        )
        assert result.returncode != 0

    def test_cleanup_after_failure(
        self, sandbox_image, sample_csv, failing_script, tmp_path,
    ):
        """Container must not exist after failed execution."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, failing_script)

        assert receipt["cleanup_verified"] is True
        result = subprocess.run(
            [DOCKER_CMD, "inspect", receipt["container_name"]],
            capture_output=True,
        )
        assert result.returncode != 0

    @pytest.mark.slow
    def test_cleanup_after_timeout(
        self, sandbox_image, sample_csv, timeout_script, tmp_path,
    ):
        """Container must not exist after timeout."""
        policy = SandboxPolicy(timeout_seconds=5)
        runner = SandboxRunner(
            policy=policy,
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, timeout_script)

        assert receipt["cleanup_verified"] is True
        result = subprocess.run(
            [DOCKER_CMD, "inspect", receipt["container_name"]],
            capture_output=True,
        )
        assert result.returncode != 0

    def test_workspace_cleaned_up(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        """Task staging directory must be removed after execution."""
        ws_dir = tmp_path / "ws"
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=ws_dir,
            receipts_dir=tmp_path / "rec",
        )
        runner.run(sample_csv, simple_script)

        # Workspace should be empty or not exist
        if ws_dir.exists():
            assert not any(ws_dir.iterdir()), "Staging directory was not cleaned up"


@requires_docker
class TestArtifacts:
    """Tests for artifact generation, validation, and capture."""

    def test_artifacts_captured(
        self, sandbox_image, sample_csv, artifact_script, tmp_path,
    ):
        """Generated artifacts must be captured via docker cp."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, artifact_script)

        assert receipt["execution"]["status"] == "completed"
        names = {a["name"] for a in receipt["artifacts"]}
        assert "chart.png" in names
        assert "summary.csv" in names
        assert "result.json" in names

    def test_artifact_hashes(
        self, sandbox_image, sample_csv, artifact_script, tmp_path,
    ):
        """Every artifact must have a valid SHA-256 hash."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, artifact_script)

        for artifact in receipt["artifacts"]:
            assert "sha256" in artifact
            assert len(artifact["sha256"]) == 64
            int(artifact["sha256"], 16)  # valid hex

    def test_artifact_metadata(
        self, sandbox_image, sample_csv, artifact_script, tmp_path,
    ):
        """Artifact entries must include name, size, sha256, and mime_type."""
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, artifact_script)

        for artifact in receipt["artifacts"]:
            assert "name" in artifact
            assert "size" in artifact
            assert "sha256" in artifact
            assert "mime_type" in artifact
            assert artifact["size"] > 0

    def test_artifacts_persisted(
        self, sandbox_image, sample_csv, artifact_script, tmp_path,
    ):
        """Artifacts must be copied to the receipt directory."""
        rec_dir = tmp_path / "rec"
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=rec_dir,
        )
        receipt = runner.run(sample_csv, artifact_script)

        task_id = receipt["task_id"]
        attempt_id = receipt["attempt_id"]
        artifacts_dir = rec_dir / f"{task_id}_{attempt_id}" / "artifacts"
        assert artifacts_dir.exists()

        persisted = {f.name for f in artifacts_dir.iterdir()}
        receipt_names = {a["name"] for a in receipt["artifacts"]}
        assert receipt_names.issubset(persisted)

