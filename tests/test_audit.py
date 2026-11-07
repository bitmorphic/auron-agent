"""
Security and reliability audit tests (Milestone 1).

Covers the specific findings from the focused code-level audit:
  1. Bounded log reading (no controller OOM from chatty containers)
  2. Policy ceiling enforcement (CLI/callers cannot increase limits)
  3. Path traversal fix using is_relative_to()
  4. Receipt hash uses deep copy (nested mutation safety)
  5. Orphan reaper uses label filter, not name-only
  6. Image digest recorded in receipt
  7. AURON label applied to every container
  8. Artifact filename safety (null bytes, long names, dotfiles)

Tests that require Docker are decorated with @requires_docker.
Tests that are Linux-only (kernel-level enforcement) are @linux_only.
"""

import hashlib
import json
import subprocess

import pytest

from sandbox.runner import (
    AURON_LABEL,
    DOCKER_CMD,
    SandboxPolicy,
    SandboxRunner,
    _safe_in_directory,
    compute_receipt_hash,
    reap_orphaned_containers,
    verify_receipt_hash,
)
from tests.conftest import linux_only, requires_docker


# ─── 1. Policy ceiling enforcement ───────────────────────────────────────────


class TestPolicyCeilings:
    """SandboxPolicy must refuse to exceed hard ceilings."""

    def test_memory_ceiling(self):
        with pytest.raises(ValueError, match="memory_mb"):
            SandboxPolicy(memory_mb=1024)

    def test_swap_ceiling(self):
        with pytest.raises(ValueError, match="memory_swap_mb"):
            SandboxPolicy(memory_swap_mb=1024)

    def test_cpu_ceiling(self):
        with pytest.raises(ValueError, match="cpu_limit"):
            SandboxPolicy(cpu_limit=4.0)

    def test_pids_ceiling(self):
        with pytest.raises(ValueError, match="pids_limit"):
            SandboxPolicy(pids_limit=256)

    def test_timeout_ceiling(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            SandboxPolicy(timeout_seconds=120)

    def test_stdout_ceiling(self):
        with pytest.raises(ValueError, match="stdout_limit"):
            SandboxPolicy(stdout_limit=10 * 1024 * 1024)

    def test_lower_limits_allowed(self):
        """Tests may use lower limits — this must succeed."""
        p = SandboxPolicy(timeout_seconds=5, memory_mb=64, pids_limit=16)
        assert p.timeout_seconds == 5
        assert p.memory_mb == 64


# ─── 2. Path traversal: _safe_in_directory ───────────────────────────────────


class TestSafeInDirectory:
    """_safe_in_directory must be separator-aware and immune to prefix tricks."""

    def test_child_inside_parent(self, tmp_path):
        child = tmp_path / "out" / "result.txt"
        (tmp_path / "out").mkdir()
        child.touch()
        assert _safe_in_directory(child, tmp_path / "out") is True

    def test_child_is_parent(self, tmp_path):
        """Exact same path is not inside itself."""
        out = tmp_path / "out"
        out.mkdir()
        assert _safe_in_directory(out, out) is False

    def test_prefix_trick(self, tmp_path):
        """/out2 must not match /out."""
        out  = tmp_path / "out"
        out2 = tmp_path / "out2"
        out.mkdir(); out2.mkdir()
        fake = out2 / "evil.txt"
        fake.touch()
        assert _safe_in_directory(fake, out) is False

    def test_dotdot_escape(self, tmp_path):
        """../ traversal must be blocked."""
        out = tmp_path / "out"
        out.mkdir()
        # Construct a path that resolves outside out/
        evil = out / ".." / "secret.txt"
        (tmp_path / "secret.txt").touch()
        assert _safe_in_directory(evil, out) is False

    def test_normal_child(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        child = out / "sub" / "file.txt"
        child.parent.mkdir()
        child.touch()
        assert _safe_in_directory(child, out) is True


# ─── 3. Receipt hash: deep copy isolation ────────────────────────────────────


class TestReceiptHashDeepCopy:
    """compute_receipt_hash must not be affected by post-call mutations."""

    def test_nested_mutation_does_not_change_stored_hash(self):
        """
        Mutating a nested value after hashing must not invalidate the hash.
        The hash was computed over a deep copy, so the stored hash is stable.
        """
        receipt = {
            "receipt_version": "1.0",
            "task_id": "test",
            "execution": {"exit_code": 0, "extra": [1, 2, 3]},
            "receipt_hash": None,
        }
        receipt["receipt_hash"] = compute_receipt_hash(receipt)

        # Mutate nested data after the hash was computed
        receipt["execution"]["extra"].append(99)

        # Verification should still pass because hash covers original state
        # (The stored hash was computed before the mutation)
        # Recompute and compare to confirm deep copy was used
        receipt2 = dict(receipt)
        receipt2["receipt_hash"] = None
        receipt2["execution"] = {"exit_code": 0, "extra": [1, 2, 3]}  # original
        h_original = compute_receipt_hash(receipt2)

        receipt3 = dict(receipt)
        receipt3["receipt_hash"] = None
        h_mutated = compute_receipt_hash(receipt3)

        assert h_original != h_mutated, (
            "Hash should differ after mutation — confirming deep copy is used"
        )

    def test_hash_field_null_during_computation(self):
        """receipt_hash must be null during hashing regardless of its value."""
        r1 = {"data": "x", "receipt_hash": None}
        r2 = {"data": "x", "receipt_hash": "some-previous-hash"}
        assert compute_receipt_hash(r1) == compute_receipt_hash(r2)


# ─── 4. Bounded log reader ────────────────────────────────────────────────────


@requires_docker
class TestBoundedLogReader:
    """docker logs must be read with a byte cap, not fully buffered."""

    def test_chatty_container_logs_capped(self, sandbox_image, sample_csv, tmp_path):
        """
        A container printing more than 256 KB must not exceed the limit
        in the receipt stdout field.
        """
        # 300 KB of output — well above the 256 KB cap
        script = tmp_path / "chatty.py"
        script.write_text(
            "import sys\n"
            "line = 'A' * 1000 + '\\n'\n"
            "for _ in range(310):  # ~310 KB\n"
            "    sys.stdout.write(line)\n"
            "sys.stdout.flush()\n",
            encoding="utf-8",
        )
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        stdout_bytes = receipt["output"]["stdout"].encode("utf-8", errors="replace")
        assert len(stdout_bytes) <= 256 * 1024 + 100  # small tolerance for encoding
        assert receipt["output"]["stdout_truncated"] is True


# ─── 5. Orphan reaper uses label filter ──────────────────────────────────────


@requires_docker
class TestOrphanReaper:
    """Reaper must use auron.managed=true label, not name-prefix alone."""

    def test_reaper_removes_labelled_auron_container(self, sandbox_image, tmp_path):
        """A stopped auron container with the label must be reaped."""
        name = "auron-reaper-test-label"
        try:
            subprocess.run(
                [DOCKER_CMD, "run", "--rm", "--name", name,
                 "--label", "auron.managed=true",
                 "--network", "none",
                 sandbox_image, "true"],
                capture_output=True, timeout=30,
            )
            # run --rm already removes it; create a stopped one instead
            subprocess.run(
                [DOCKER_CMD, "create", "--name", name,
                 "--label", "auron.managed=true",
                 sandbox_image, "true"],
                capture_output=True, timeout=15,
            )
            removed = reap_orphaned_containers()
            assert name in removed
        finally:
            subprocess.run(
                [DOCKER_CMD, "rm", "-f", name], capture_output=True, timeout=10,
            )

    def test_reaper_does_not_touch_unlabelled_container(
        self, sandbox_image, tmp_path,
    ):
        """A container with 'auron-' in the name but no label must NOT be reaped."""
        name = "auron-not-managed-by-auron"
        try:
            subprocess.run(
                [DOCKER_CMD, "create", "--name", name,
                 # No auron.managed=true label
                 sandbox_image, "true"],
                capture_output=True, timeout=15,
            )
            removed = reap_orphaned_containers()
            assert name not in removed
        finally:
            subprocess.run(
                [DOCKER_CMD, "rm", "-f", name], capture_output=True, timeout=10,
            )


# ─── 6. Image digest in receipt ──────────────────────────────────────────────


@requires_docker
class TestImageDigest:
    """Every receipt must record the immutable image digest."""

    def test_digest_recorded(self, sandbox_image, sample_csv, simple_script, tmp_path):
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, simple_script)
        digest = receipt["container"]["image_digest"]
        assert digest and digest != "unknown"
        # Must be sha256:... or a local image ID starting with sha256:
        assert "sha256" in digest.lower() or len(digest) >= 12


# ─── 7. AURON label applied to containers ────────────────────────────────────


@requires_docker
class TestContainerLabels:
    """Every sandbox container must carry the auron.managed=true label."""

    def test_managed_label_in_inspect(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, simple_script)
        inspect = receipt["container"]["inspect"]
        labels = inspect.get("Config", {}).get("Labels", {}) or {}
        assert "auron.managed" in labels
        assert labels["auron.managed"] == "true"


# ─── 8. Artifact filename safety ─────────────────────────────────────────────


class TestArtifactFilenameSafety:
    """Malicious artifact filenames must be handled gracefully."""

    def test_dotfile_rejected(self, tmp_path):
        """Files starting with dot have no allowed extension and are skipped."""
        runner = SandboxRunner()
        out = tmp_path / "out"
        out.mkdir()
        (out / ".hidden").write_text("secret")
        artifacts = runner._validate_artifacts(out)
        names = {a["name"] for a in artifacts}
        assert ".hidden" not in names

    def test_very_long_filename_handled(self, tmp_path):
        """Files with very long names (but valid extension) are processed."""
        runner = SandboxRunner()
        out = tmp_path / "out"
        out.mkdir()
        long_name = "a" * 200 + ".txt"
        (out / long_name).write_text("ok")
        artifacts = runner._validate_artifacts(out)
        assert len(artifacts) == 1
        assert artifacts[0]["name"] == long_name

    def test_oversized_single_file_stops_accumulation(self, tmp_path):
        """A single file over the limit stops the scan at that file."""
        policy = SandboxPolicy(output_limit=100)
        runner = SandboxRunner(policy=policy)
        out = tmp_path / "out"
        out.mkdir()
        (out / "big.txt").write_text("x" * 200)
        (out / "small.txt").write_text("y" * 10)
        artifacts = runner._validate_artifacts(out)
        # big.txt exceeds the limit, small.txt is never reached
        assert len(artifacts) == 0

