"""Security control tests for the AURON sandbox.

These tests verify that the Docker container security profile specified
in PRD §11 is correctly applied.  Checks are performed both by running
scripts inside the sandbox (observing from the inside) and by inspecting
the container configuration from outside (Docker inspect).

Tests marked ``linux_only`` require a Linux Docker host for accurate
enforcement and must be validated on the Vultr Ubuntu VM.
"""

import json
import subprocess

import pytest

from sandbox.runner import SandboxPolicy, SandboxRunner
from tests.conftest import linux_only, requires_docker


# ─── Container inspection tests ──────────────────────────────────────────────


@requires_docker
class TestContainerInspection:
    """Verify security controls via Docker inspect data."""

    def _run_noop(self, sandbox_image, tmp_path) -> dict:
        """Run a no-op script and return the receipt."""
        script = tmp_path / "noop.py"
        script.write_text("print('ok')\n", encoding="utf-8")
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("a,b\n1,2\n", encoding="utf-8")
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        return runner.run(csv_file, script)

    def test_network_none(self, sandbox_image, tmp_path):
        """HostConfig.NetworkMode must be 'none'."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert inspect["HostConfig"]["NetworkMode"] == "none"

    def test_readonly_rootfs(self, sandbox_image, tmp_path):
        """HostConfig.ReadonlyRootfs must be True."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert inspect["HostConfig"]["ReadonlyRootfs"] is True

    def test_user_set(self, sandbox_image, tmp_path):
        """Config.User must be '10001:10001'."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert inspect["Config"]["User"] == "10001:10001"

    def test_cap_drop_all(self, sandbox_image, tmp_path):
        """HostConfig.CapDrop must include 'ALL'."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert "ALL" in inspect["HostConfig"]["CapDrop"]

    def test_no_new_privileges(self, sandbox_image, tmp_path):
        """HostConfig.SecurityOpt must include 'no-new-privileges'."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert "no-new-privileges" in inspect["HostConfig"]["SecurityOpt"]

    def test_memory_limit(self, sandbox_image, tmp_path):
        """HostConfig.Memory must be 512 MB."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert inspect["HostConfig"]["Memory"] == 512 * 1024 * 1024

    def test_memory_swap_limit(self, sandbox_image, tmp_path):
        """HostConfig.MemorySwap must equal memory (no extra swap)."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert inspect["HostConfig"]["MemorySwap"] == 512 * 1024 * 1024

    def test_pids_limit(self, sandbox_image, tmp_path):
        """HostConfig.PidsLimit must be 64."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert inspect["HostConfig"]["PidsLimit"] == 64

    def test_cpu_limit(self, sandbox_image, tmp_path):
        """CPU limit must be configured (NanoCpus)."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        nano_cpus = inspect["HostConfig"]["NanoCpus"]
        assert nano_cpus == 1_000_000_000  # 1 CPU in nanocpus

    def test_no_docker_socket_mount(self, sandbox_image, tmp_path):
        """Docker socket must not be mounted."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        mounts = inspect.get("Mounts", [])
        for mount in mounts:
            source = mount.get("Source", "")
            assert "docker.sock" not in source, (
                f"Docker socket found in mounts: {source}"
            )

    def test_no_privileged(self, sandbox_image, tmp_path):
        """Container must not be privileged."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert inspect["HostConfig"]["Privileged"] is False

    def test_no_host_network(self, sandbox_image, tmp_path):
        """Container must not use host networking."""
        receipt = self._run_noop(sandbox_image, tmp_path)
        inspect = receipt["container"]["inspect"]
        assert inspect["HostConfig"]["NetworkMode"] != "host"


# ─── In-container observation tests ──────────────────────────────────────────


@requires_docker
class TestInContainerObservations:
    """Verify security controls by running scripts inside the sandbox."""

    def test_nonroot_uid(self, sandbox_image, sample_csv, tmp_path):
        """Process must run as UID 10001."""
        script = tmp_path / "uid.py"
        script.write_text(
            "import os, json\n"
            "print(json.dumps({'uid': os.getuid(), 'gid': os.getgid()}))\n",
            encoding="utf-8",
        )
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        assert receipt["execution"]["exit_code"] == 0
        result = json.loads(receipt["output"]["stdout"])
        assert result["uid"] == 10001
        assert result["gid"] == 10001

    def test_network_blocked(self, sandbox_image, sample_csv, tmp_path):
        """Network connections must fail."""
        script = tmp_path / "net.py"
        script.write_text(
            "import socket, json, sys\n"
            "try:\n"
            "    s = socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
            "    s.close()\n"
            "    print(json.dumps({'net': 'open'}))\n"
            "    sys.exit(1)\n"
            "except (socket.error, OSError) as e:\n"
            "    print(json.dumps({'net': 'blocked', 'err': str(e)}))\n",
            encoding="utf-8",
        )
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        result = json.loads(receipt["output"]["stdout"])
        assert result["net"] == "blocked"

    def test_readonly_root_from_inside(
        self, sandbox_image, sample_csv, tmp_path,
    ):
        """Writes to root filesystem locations must fail."""
        script = tmp_path / "write.py"
        script.write_text(
            "import json\n"
            "results = {}\n"
            "for p in ['/etc/x', '/var/x', '/home/x']:\n"
            "    try:\n"
            "        open(p, 'w').write('x')\n"
            "        results[p] = 'writable'\n"
            "    except (OSError, PermissionError) as e:\n"
            "        results[p] = 'blocked'\n"
            "# /tmp and /out should be writable (tmpfs)\n"
            "for p in ['/tmp/x', '/out/x.txt']:\n"
            "    try:\n"
            "        open(p, 'w').write('x')\n"
            "        results[p] = 'writable'\n"
            "    except (OSError, PermissionError) as e:\n"
            "        results[p] = 'blocked'\n"
            "print(json.dumps(results))\n",
            encoding="utf-8",
        )
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        assert receipt["execution"]["exit_code"] == 0
        results = json.loads(receipt["output"]["stdout"])

        # Root FS blocked
        for path in ["/etc/x", "/var/x", "/home/x"]:
            assert results[path] == "blocked", f"{path} should not be writable"

        # tmpfs locations writable
        assert results["/tmp/x"] == "writable"
        assert results["/out/x.txt"] == "writable"

    def test_input_readonly(self, sandbox_image, sample_csv, tmp_path):
        """Input CSV mount must be read-only."""
        script = tmp_path / "write_input.py"
        script.write_text(
            "import json\n"
            "try:\n"
            "    open('/data/input.csv', 'a').write('injected')\n"
            "    print(json.dumps({'writable': True}))\n"
            "except (OSError, PermissionError):\n"
            "    print(json.dumps({'writable': False}))\n",
            encoding="utf-8",
        )
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        result = json.loads(receipt["output"]["stdout"])
        assert result["writable"] is False

    def test_no_docker_socket_inside(
        self, sandbox_image, sample_csv, tmp_path,
    ):
        """Docker socket must not be visible inside the container."""
        script = tmp_path / "sock.py"
        script.write_text(
            "import os, json\n"
            "print(json.dumps({'sock': os.path.exists('/var/run/docker.sock')}))\n",
            encoding="utf-8",
        )
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        result = json.loads(receipt["output"]["stdout"])
        assert result["sock"] is False

    def test_environment_allowlist(
        self, sandbox_image, sample_csv, tmp_path,
    ):
        """Only allow-listed env vars should be present; no secrets."""
        script = tmp_path / "env.py"
        script.write_text(
            "import os, json\n"
            "print(json.dumps(dict(os.environ)))\n",
            encoding="utf-8",
        )
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        env = json.loads(receipt["output"]["stdout"])
        assert env.get("MPLBACKEND") == "Agg"
        assert env.get("MPLCONFIGDIR") == "/tmp/mpl"
        assert env.get("PYTHONDONTWRITEBYTECODE") == "1"

        # No secrets
        sensitive = ["API_KEY", "SECRET", "PASSWORD", "TOKEN", "CREDENTIAL"]
        for key in env:
            for pattern in sensitive:
                assert pattern not in key.upper(), (
                    f"Sensitive env var leaked: {key}"
                )


# ─── Resource limit enforcement ──────────────────────────────────────────────


@requires_docker
class TestResourceLimits:
    """Test enforcement of resource limits."""

    @linux_only
    @pytest.mark.slow
    def test_oom_kill(self, sandbox_image, sample_csv, tmp_path):
        """OOM kill must be detected when memory limit is exceeded.

        NOTE: This test uses a reduced memory limit (64 MB) for speed.
        Final validation with production limits on Vultr Ubuntu VM.
        """
        script = tmp_path / "oom.py"
        script.write_text(
            "data = []\n"
            "while True:\n"
            "    data.append(b'x' * (1024 * 1024))  # 1 MB chunks\n",
            encoding="utf-8",
        )
        policy = SandboxPolicy(
            memory_mb=64, memory_swap_mb=64, timeout_seconds=30,
        )
        runner = SandboxRunner(
            policy=policy,
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        # Container should have been OOM-killed
        assert receipt["execution"]["oom_killed"] is True
        assert receipt["execution"]["status"] == "oom_killed"
        assert receipt["cleanup_verified"] is True


# ─── Artifact validation (no Docker required) ────────────────────────────────


class TestArtifactPathSecurity:
    """Test artifact path validation — runs without Docker."""

    def test_symlink_rejected(self, tmp_path):
        """Symlinks in output directory must be rejected."""
        from sandbox.runner import SandboxRunner

        runner = SandboxRunner()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Normal file
        normal = output_dir / "result.txt"
        normal.write_text("data", encoding="utf-8")

        # Symlink pointing outside
        target = tmp_path / "secret.txt"
        target.write_text("sensitive", encoding="utf-8")
        link = output_dir / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        artifacts = runner._validate_artifacts(output_dir)
        names = {a["name"] for a in artifacts}

        assert "result.txt" in names
        assert "link.txt" not in names

    def test_extension_allowlist_enforced(self, tmp_path):
        """Only .png, .csv, .md, .json, .txt extensions are allowed."""
        from sandbox.runner import SandboxRunner

        runner = SandboxRunner()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        (output_dir / "ok.json").write_text("{}", encoding="utf-8")
        (output_dir / "ok.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (output_dir / "ok.txt").write_text("text", encoding="utf-8")
        (output_dir / "ok.md").write_text("# md", encoding="utf-8")
        (output_dir / "bad.exe").write_bytes(b"\x00" * 10)
        (output_dir / "bad.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (output_dir / "bad.pkl").write_bytes(b"\x00" * 10)
        (output_dir / "bad.py").write_text("import os\n", encoding="utf-8")

        artifacts = runner._validate_artifacts(output_dir)
        names = {a["name"] for a in artifacts}

        assert {"ok.json", "ok.csv", "ok.txt", "ok.md"}.issubset(names)
        assert names.isdisjoint({"bad.exe", "bad.sh", "bad.pkl", "bad.py"})

    def test_png_magic_bytes_validated(self, tmp_path):
        """PNG files must have valid magic bytes."""
        from sandbox.runner import SandboxRunner

        runner = SandboxRunner()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Valid PNG header
        (output_dir / "valid.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
        )
        # Invalid PNG
        (output_dir / "fake.png").write_bytes(b"not-a-png-file")

        artifacts = runner._validate_artifacts(output_dir)
        names = {a["name"] for a in artifacts}

        assert "valid.png" in names
        assert "fake.png" not in names

    def test_csv_artifact_validated(self, tmp_path):
        """CSV artifacts must be parseable."""
        from sandbox.runner import SandboxRunner

        runner = SandboxRunner()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Valid CSV
        (output_dir / "good.csv").write_text(
            "a,b\n1,2\n3,4\n", encoding="utf-8",
        )
        # Invalid CSV (binary garbage with .csv extension)
        (output_dir / "bad.csv").write_bytes(b"\xff\xfe" + b"\x00" * 100)

        artifacts = runner._validate_artifacts(output_dir)
        names = {a["name"] for a in artifacts}

        assert "good.csv" in names
        assert "bad.csv" not in names

    def test_output_size_limit(self, tmp_path):
        """Artifacts beyond the total size limit must be excluded."""
        from sandbox.runner import SandboxPolicy, SandboxRunner

        policy = SandboxPolicy(output_limit=500)  # tiny limit for test
        runner = SandboxRunner(policy=policy)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        (output_dir / "a.txt").write_text("x" * 300, encoding="utf-8")
        (output_dir / "b.txt").write_text("y" * 300, encoding="utf-8")

        artifacts = runner._validate_artifacts(output_dir)

        # Only the first file should fit within the 500-byte limit
        assert len(artifacts) == 1
        assert artifacts[0]["name"] == "a.txt"


# ─── Execution-time output storage enforcement (PRD §11) ─────────────────────


@requires_docker
class TestExecutionTimeOutputLimit:
    """Validate that /out storage is strictly bounded DURING execution."""

    def test_script_cannot_write_beyond_output_limit(
        self, sandbox_image, sample_csv, tmp_path,
    ):
        """
        A script attempting to write >20 MB to /out must receive an OSError
        (ENOSPC: No space left on device) during execution.
        Zero bytes of the oversized file must touch the host disk.
        """
        from sandbox.runner import SandboxRunner

        script = tmp_path / "bomb.py"
        script.write_text(
            "import os, sys\n"
            "try:\n"
            "    with open('/out/bomb.bin', 'wb') as f:\n"
            "        f.write(b'A' * (25 * 1024 * 1024))  # 25 MB > 20 MB tmpfs cap\n"
            "    sys.exit(0)  # Should NOT succeed\n"
            "except OSError as e:\n"
            "    # Kernel-enforced ENOSPC during execution\n"
            "    print(f'CAUGHT_ENOSPC: {e}', file=sys.stderr)\n"
            "    sys.exit(42)\n",
            encoding="utf-8",
        )
        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, script)

        # The script caught the kernel ENOSPC and exited with 42
        assert receipt["execution"]["exit_code"] == 42
        assert "CAUGHT_ENOSPC" in receipt["output"]["stderr"]
        assert "No space left on device" in receipt["output"]["stderr"]
        assert receipt["execution"]["status"] == "failed"

        # The host output directory was NOT filled with a 25MB file
        ws_dir = tmp_path / "ws"
        if ws_dir.exists():
            for f in ws_dir.rglob("*"):
                if f.is_file():
                    assert f.stat().st_size < 20 * 1024 * 1024

    def test_out_is_tmpfs_in_inspect(
        self, sandbox_image, sample_csv, simple_script, tmp_path,
    ):
        """Container inspection data must confirm /out is a mounted tmpfs."""
        from sandbox.runner import SandboxRunner

        runner = SandboxRunner(
            image_tag="test",
            workspace_dir=tmp_path / "ws",
            receipts_dir=tmp_path / "rec",
        )
        receipt = runner.run(sample_csv, simple_script)
        inspect = receipt["container"]["inspect"]
        tmpfs_mounts = inspect.get("HostConfig", {}).get("Tmpfs", {}) or {}
        assert "/out" in tmpfs_mounts, f"/out not found in Tmpfs mounts: {tmpfs_mounts}"

