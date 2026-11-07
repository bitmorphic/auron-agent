"""
AURON Sandbox Runner — Secure Python execution in disposable Docker containers.

This is the trusted controller.  It owns the security profile.
The model cannot modify container flags, limits, mounts, or environment.
CLI users cannot override or increase security-critical resource limits.

Usage:
    python -m sandbox.runner <csv_path> <script_path> [--image-tag TAG]

Architecture:
    CSV + script → task-specific staging → fresh Docker container →
    restricted Python execution → captured execution evidence →
    artifact validation + hashing → trusted receipt JSON → cleanup.

Container lifecycle:
    docker create  (with fixed security profile + AURON labels)
    → docker start
    → docker wait  (with wall-clock timeout via threading.Timer)
    → docker logs  (bounded read — stdout/stderr capped at 256 KB each)
    → docker inspect  (OOM / State evidence, captured before removal)
    → artifact extraction  (streamed from /out tmpfs before removal)
    → artifact validation  (extension allowlist, magic bytes, size cap, hashes)
    → docker rm -f  (guaranteed via ContainerCleanupGuard)

Security notes:
    - /out uses a 20 MB bounded tmpfs (rw,noexec,nosuid). Storage limit is
      kernel-enforced DURING execution. Attempts to write >20 MB fail with
      ENOSPC. Artifacts are safely streamed via controller tar stream before
      container exit. Zero bytes touch host output directory during execution.
    - docker logs is read with a byte-limit so a chatty process cannot
      exhaust controller memory before truncation.
    - All subprocess calls use argument arrays — never shell=True.
    - Containers are labelled auron.managed=true for safe orphan reaping.
"""

import argparse
import copy
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ─── Constants ────────────────────────────────────────────────────────────────

MAX_STREAM_BYTES = 256 * 1024          # 256 KB per stdout/stderr stream
MAX_OUTPUT_BYTES = 20 * 1024 * 1024    # 20 MB total artifact output (post-run)
ALLOWED_EXTENSIONS = frozenset({".png", ".csv", ".md", ".json", ".txt"})
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SANDBOX_IMAGE = "auron-sandbox"
CONTAINER_PREFIX = "auron-"
# Label applied to every managed container — used by the reaper in addition
# to the name prefix, so we never accidentally remove unrelated containers.
AURON_LABEL = "auron.managed=true"
WORKSPACE_DIR = Path("workspace")
RECEIPTS_DIR = Path("receipts")

MIME_TYPES = {
    ".png": "image/png",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".json": "application/json",
    ".txt": "text/plain",
}


# ─── Docker discovery ─────────────────────────────────────────────────────────


def _find_docker() -> str:
    """Locate the Docker CLI executable.

    Checks PATH first, then common Docker Desktop install locations on
    Windows.  Returns ``"docker"`` if found on PATH, or the full path
    to the executable otherwise.
    """
    if shutil.which("docker"):
        return "docker"

    if platform.system() == "Windows":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        ]
        for c in candidates:
            if c.is_file():
                return str(c)

    raise FileNotFoundError(
        "Docker CLI not found.  Install Docker Desktop and ensure "
        "docker.exe is on your PATH, or install Docker Engine on Linux."
    )


DOCKER_CMD = _find_docker()


# ─── Security Policy ─────────────────────────────────────────────────────────


class SandboxPolicy:
    """
    Fixed security policy for sandbox execution.

    Controller-owned — the model cannot modify any of these values.
    CLI users cannot override or increase security-critical limits.
    Tests may supply *lower* limits (e.g. shorter timeout) but the
    constructor raises ValueError if any limit exceeds the safe ceiling.
    """

    # Hard ceilings — no run may exceed these values.
    _MAX_MEMORY_MB    = 512
    _MAX_SWAP_MB      = 512
    _MAX_CPU          = 1.0
    _MAX_PIDS         = 64
    _MAX_TIMEOUT      = 60
    _MAX_STDOUT_BYTES = MAX_STREAM_BYTES
    _MAX_STDERR_BYTES = MAX_STREAM_BYTES
    _MAX_OUTPUT_BYTES = MAX_OUTPUT_BYTES

    def __init__(
        self,
        network: str = "none",
        read_only_root: bool = True,
        user: str = "10001:10001",
        cap_drop: tuple[str, ...] = ("ALL",),
        no_new_privileges: bool = True,
        memory_mb: int = 512,
        memory_swap_mb: int = 512,
        cpu_limit: float = 1.0,
        pids_limit: int = 64,
        tmpfs_tmp_size_mb: int = 64,
        timeout_seconds: int = 60,
        stdout_limit: int = MAX_STREAM_BYTES,
        stderr_limit: int = MAX_STREAM_BYTES,
        output_limit: int = MAX_OUTPUT_BYTES,
        env_allowlist: tuple[tuple[str, str], ...] = (
            ("MPLBACKEND", "Agg"),
            ("MPLCONFIGDIR", "/tmp/mpl"),
            ("PYTHONDONTWRITEBYTECODE", "1"),
        ),
    ) -> None:
        # Enforce ceilings — CLI users / tests cannot increase limits
        if memory_mb > self._MAX_MEMORY_MB:
            raise ValueError(f"memory_mb {memory_mb} exceeds ceiling {self._MAX_MEMORY_MB}")
        if memory_swap_mb > self._MAX_SWAP_MB:
            raise ValueError(f"memory_swap_mb {memory_swap_mb} exceeds ceiling {self._MAX_SWAP_MB}")
        if cpu_limit > self._MAX_CPU:
            raise ValueError(f"cpu_limit {cpu_limit} exceeds ceiling {self._MAX_CPU}")
        if pids_limit > self._MAX_PIDS:
            raise ValueError(f"pids_limit {pids_limit} exceeds ceiling {self._MAX_PIDS}")
        if timeout_seconds > self._MAX_TIMEOUT:
            raise ValueError(f"timeout_seconds {timeout_seconds} exceeds ceiling {self._MAX_TIMEOUT}")
        if stdout_limit > self._MAX_STDOUT_BYTES:
            raise ValueError(f"stdout_limit {stdout_limit} exceeds ceiling")
        if stderr_limit > self._MAX_STDERR_BYTES:
            raise ValueError(f"stderr_limit {stderr_limit} exceeds ceiling")
        if output_limit > self._MAX_OUTPUT_BYTES:
            raise ValueError(f"output_limit {output_limit} exceeds ceiling")

        self.network          = network
        self.read_only_root   = read_only_root
        self.user             = user
        self.cap_drop         = cap_drop
        self.no_new_privileges = no_new_privileges
        self.memory_mb        = memory_mb
        self.memory_swap_mb   = memory_swap_mb
        self.cpu_limit        = cpu_limit
        self.pids_limit       = pids_limit
        self.tmpfs_tmp_size_mb = tmpfs_tmp_size_mb
        self.timeout_seconds  = timeout_seconds
        self.stdout_limit     = stdout_limit
        self.stderr_limit     = stderr_limit
        self.output_limit     = output_limit
        self.env_allowlist    = env_allowlist


DEFAULT_POLICY = SandboxPolicy()


# ─── Utility functions ───────────────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def validate_png(path: Path) -> bool:
    """Return True if the file starts with valid PNG magic bytes."""
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_MAGIC
    except OSError:
        return False


def validate_csv_artifact(path: Path) -> bool:
    """Return True if the file is parseable as CSV (first 10 rows)."""
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for i, _row in enumerate(reader):
                if i >= 10:
                    break
        return True
    except (csv.Error, UnicodeDecodeError, OSError):
        return False


def _read_bounded(proc: "subprocess.Popen[bytes]", limit: int) -> tuple[bytes, bytes]:
    """
    Read stdout and stderr from a running/finished process, bounded to
    ``limit`` bytes each.

    Uses ``communicate()`` with a capped read so that a chatty sandbox
    process cannot exhaust controller memory before truncation.
    Docker daemon logs are stored server-side; this limit only bounds
    what the controller reads into RAM.

    Args:
        proc:  An already-started Popen object.
        limit: Maximum bytes to read per stream.

    Returns:
        (stdout_bytes, stderr_bytes) — each at most ``limit`` bytes.
    """
    # communicate() reads all output — to bound it we need to read manually.
    # We collect up to `limit` bytes from each stream then drain the rest.
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_read = 0
    stderr_read = 0

    import select as _select
    import io

    # On Windows select() doesn't work on pipes; use threads instead.
    if platform.system() == "Windows":
        def _drain(stream, chunks, max_bytes, counter_ref):
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    remaining = max_bytes - counter_ref[0]
                    if remaining > 0:
                        take = chunk[:remaining]
                        chunks.append(take)
                        counter_ref[0] += len(take)
            except Exception:
                pass

        stdout_ctr = [0]
        stderr_ctr = [0]
        t1 = threading.Thread(
            target=_drain,
            args=(proc.stdout, stdout_chunks, limit, stdout_ctr),
            daemon=True,
        )
        t2 = threading.Thread(
            target=_drain,
            args=(proc.stderr, stderr_chunks, limit, stderr_ctr),
            daemon=True,
        )
        t1.start(); t2.start()
        t1.join(); t2.join()
    else:
        fds = {proc.stdout.fileno(): (stdout_chunks, [stdout_read]),
               proc.stderr.fileno(): (stderr_chunks, [stderr_read])}
        open_fds = set(fds.keys())
        while open_fds:
            try:
                readable, _, _ = _select.select(list(open_fds), [], [], 1.0)
            except Exception:
                break
            for fd in readable:
                chunks, ctr = fds[fd]
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    open_fds.discard(fd)
                    continue
                if not data:
                    open_fds.discard(fd)
                    continue
                remaining = limit - ctr[0]
                if remaining > 0:
                    take = data[:remaining]
                    chunks.append(take)
                    ctr[0] += len(take)
                # Drain but discard bytes beyond limit
        proc.wait()

    return b"".join(stdout_chunks), b"".join(stderr_chunks)


def truncate_output(data: bytes, limit: int) -> tuple[str, bool]:
    """Truncate output bytes to limit, return (decoded_text, was_truncated)."""
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False
    return data[:limit].decode("utf-8", errors="replace"), True


def compute_receipt_hash(receipt: dict) -> str:
    """
    Compute SHA-256 hash over a deep copy of the receipt with
    ``receipt_hash`` set to ``null``.

    Uses deep copy so nested mutable values cannot affect the hash of a
    separate receipt.  Provides tamper-evidence for the serialized receipt;
    it is *not* a digital signature or proof against a compromised host.
    """
    receipt_copy = copy.deepcopy(receipt)
    receipt_copy["receipt_hash"] = None
    canonical = json.dumps(
        receipt_copy,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_receipt_hash(receipt: dict) -> bool:
    """Return True if the stored receipt hash matches the recomputed hash."""
    stored = receipt.get("receipt_hash")
    if not stored:
        return False
    return compute_receipt_hash(receipt) == stored


def _safe_in_directory(child: Path, parent: Path) -> bool:
    """
    Return True only if ``child`` is strictly inside ``parent``.

    Uses ``Path.is_relative_to()`` on a resolved path, which is
    separator-aware and immune to the ``str.startswith`` prefix-match
    bug (e.g. ``/out2`` falsely matching ``/out``).
    """
    try:
        resolved_child  = child.resolve()
        resolved_parent = parent.resolve()
        return resolved_child != resolved_parent and resolved_child.is_relative_to(resolved_parent)
    except Exception:
        return False


# ─── Container Cleanup Guard ─────────────────────────────────────────────────


class ContainerCleanupGuard:
    """Context manager that guarantees container removal on all exit paths."""

    def __init__(self, container_name: str) -> None:
        self.container_name = container_name
        self._removed = False
        self._lock = threading.Lock()

    def remove(self) -> bool:
        """Force-remove the container.  Returns True if removal succeeded."""
        with self._lock:
            if self._removed:
                return True
            try:
                result = subprocess.run(
                    [DOCKER_CMD, "rm", "-f", self.container_name],
                    capture_output=True,
                    timeout=30,
                )
                self._removed = result.returncode == 0
                return self._removed
            except Exception:
                return False

    def __enter__(self) -> "ContainerCleanupGuard":
        return self

    def __exit__(self, *args: Any) -> None:
        self.remove()


# ─── Sandbox Runner ──────────────────────────────────────────────────────────


class SandboxRunner:
    """
    Trusted sandbox controller for AURON.

    Executes Python scripts in disposable Docker containers with a fixed
    security profile.  All container configuration is owned by this
    controller — the model cannot influence Docker flags, limits, mounts,
    or environment variables.
    """

    def __init__(
        self,
        policy: SandboxPolicy = DEFAULT_POLICY,
        image_tag: str = "latest",
        workspace_dir: Path | None = None,
        receipts_dir: Path | None = None,
    ) -> None:
        self.policy = policy
        if ":" in image_tag:
            self.image_name = image_tag
            self.image_tag = image_tag.split(":")[-1]
        else:
            self.image_tag = image_tag
            self.image_name = f"{SANDBOX_IMAGE}:{image_tag}"
        self.workspace_dir = Path(workspace_dir) if workspace_dir else WORKSPACE_DIR
        self.receipts_dir  = Path(receipts_dir)  if receipts_dir  else RECEIPTS_DIR

    # ── Public entry point ────────────────────────────────────────────────

    def run(self, csv_path: Path, script_path: Path) -> dict:
        """
        Execute a Python script against a CSV in a secure sandbox.

        Returns the execution receipt as a dictionary.
        """
        csv_path    = Path(csv_path).resolve()
        script_path = Path(script_path).resolve()

        if not csv_path.is_file():
            raise FileNotFoundError(f"CSV not found: {csv_path}")
        if not script_path.is_file():
            raise FileNotFoundError(f"Script not found: {script_path}")

        task_id      = str(uuid.uuid4())
        attempt_id   = str(uuid.uuid4())
        container_name = f"{CONTAINER_PREFIX}{attempt_id}"

        task_dir   = self.workspace_dir / task_id / attempt_id
        input_dir  = task_dir / "input"
        code_dir   = task_dir / "code"
        output_dir = task_dir / "output"

        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            code_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            staged_csv  = input_dir / "input.csv"
            staged_code = code_dir  / "code.py"
            shutil.copy2(csv_path,    staged_csv)
            shutil.copy2(script_path, staged_code)
            staged_csv.chmod(0o644)
            staged_code.chmod(0o644)

            code_content = staged_code.read_text(encoding="utf-8")
            input_hash   = sha256_file(staged_csv)
            image_digest = self._get_image_digest()

            result = self._execute_container(
                container_name=container_name,
                input_file=staged_csv,
                code_file=staged_code,
                output_dir=output_dir,
            )

            receipt = self._build_receipt(
                task_id=task_id,
                attempt_id=attempt_id,
                container_name=container_name,
                code=code_content,
                input_hash=input_hash,
                image_digest=image_digest,
                result=result,
            )

            receipt_dir = self._save_receipt(receipt, task_id, attempt_id)
            self._persist_artifacts(output_dir, receipt_dir)
            return receipt

        finally:
            self._cleanup_task_dir(task_dir)

    # ── Container lifecycle ───────────────────────────────────────────────

    def _execute_container(
        self,
        container_name: str,
        input_file: Path,
        code_file: Path,
        output_dir: Path,
    ) -> dict:
        """Create → start → wait → logs → inspect → validate → rm."""
        policy = self.policy
        uid, gid = (
            policy.user.split(":") if ":" in policy.user
            else (policy.user, policy.user)
        )

        # ── docker create ──
        cmd: list[str] = [
            DOCKER_CMD, "create",
            "--name", container_name,
            # Label every managed container for safe orphan reaping
            "--label", AURON_LABEL,
            "--label", f"auron.attempt={container_name}",
            "--network", policy.network,
        ]

        if policy.read_only_root:
            cmd.append("--read-only")

        for cap in policy.cap_drop:
            cmd.extend(["--cap-drop", cap])

        if policy.no_new_privileges:
            cmd.extend(["--security-opt", "no-new-privileges"])

        cmd.extend(["--user",        policy.user])
        cmd.extend(["--pids-limit",  str(policy.pids_limit)])
        cmd.extend(["--memory",      f"{policy.memory_mb}m"])
        cmd.extend(["--memory-swap", f"{policy.memory_swap_mb}m"])
        cmd.extend(["--cpus",        str(policy.cpu_limit)])

        # /tmp — bounded tmpfs, noexec, nosuid.
        # uid/gid so the non-root user can write (Docker tmpfs ignores
        # Dockerfile-level chown at mount time).
        cmd.extend([
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={policy.tmpfs_tmp_size_mb}m,"
            f"uid={uid},gid={gid}",
        ])

        # /out — bounded tmpfs, noexec, nosuid.
        # Kernel-enforced during execution. Zero bytes touch host disk.
        out_size_mb = max(1, policy.output_limit // (1024 * 1024))
        cmd.extend([
            "--tmpfs",
            f"/out:rw,noexec,nosuid,size={out_size_mb}m,"
            f"uid={uid},gid={gid}",
        ])

        for env_name, env_value in policy.env_allowlist:
            cmd.extend(["-e", f"{env_name}={env_value}"])

        # Input and code — read-only bind mounts
        cmd.extend(["-v", f"{input_file.resolve()}:/data/input.csv:ro"])
        cmd.extend(["-v", f"{code_file.resolve()}:/app/code.py:ro"])

        wrapper_cmd = (
            "python /app/code.py; code=$?; echo $code > /tmp/auron_done; "
            "while [ ! -f /tmp/auron_ack ]; do sleep 0.05; done; exit $code"
        )
        cmd.extend([self.image_name, "sh", "-c", wrapper_cmd])

        create_result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if create_result.returncode != 0:
            raise RuntimeError(
                f"docker create failed: {create_result.stderr.strip()}"
            )

        # ── Guaranteed cleanup from this point on ──
        with ContainerCleanupGuard(container_name) as guard:

            # ── docker start ──
            start_time = time.monotonic()
            started_at = datetime.now(timezone.utc).isoformat()

            start_result = subprocess.run(
                [DOCKER_CMD, "start", container_name],
                capture_output=True, text=True, timeout=30,
            )
            if start_result.returncode != 0:
                raise RuntimeError(
                    f"docker start failed: {start_result.stderr.strip()}"
                )

            # ── Wall-clock timeout via threading.Timer ──
            timed_out = threading.Event()

            def _kill_on_timeout() -> None:
                timed_out.set()
                try:
                    subprocess.run(
                        [DOCKER_CMD, "kill", container_name],
                        capture_output=True, timeout=15,
                    )
                except Exception:
                    pass

            timer = threading.Timer(policy.timeout_seconds, _kill_on_timeout)
            timer.daemon = True
            timer.start()

            exit_code = -1
            poll_interval = 0.05
            max_poll_time = policy.timeout_seconds + 5
            poll_start = time.monotonic()

            while (time.monotonic() - poll_start) < max_poll_time:
                if timed_out.is_set():
                    break

                # Check if Python completed and recorded exit code
                try:
                    res = subprocess.run(
                        [DOCKER_CMD, "exec", container_name, "cat", "/tmp/auron_done"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        try:
                            exit_code = int(res.stdout.strip().splitlines()[0])
                        except ValueError:
                            exit_code = 0
                        break
                except Exception:
                    pass

                # Check if container stopped unexpectedly (OOM kill, early crash)
                try:
                    inspect_proc = subprocess.run(
                        [DOCKER_CMD, "inspect", "--format", "{{.State.Running}}", container_name],
                        capture_output=True, text=True, timeout=5,
                    )
                    if inspect_proc.returncode != 0 or inspect_proc.stdout.strip() != "true":
                        break
                except Exception:
                    break

                time.sleep(poll_interval)

            # Safely extract artifacts while container is still alive
            if not timed_out.is_set():
                self._extract_artifacts_stream(container_name, output_dir)
                # Release container to exit cleanly with Python's exit code
                try:
                    subprocess.run(
                        [DOCKER_CMD, "exec", container_name, "touch", "/tmp/auron_ack"],
                        capture_output=True, timeout=5,
                    )
                except Exception:
                    pass

            # Wait for container process to terminate
            try:
                wait_result = subprocess.run(
                    [DOCKER_CMD, "wait", container_name],
                    capture_output=True, text=True,
                    timeout=policy.timeout_seconds + 20,
                )
                if exit_code == -1:
                    try:
                        exit_code = int(wait_result.stdout.strip())
                    except ValueError:
                        pass
            except subprocess.TimeoutExpired:
                if not timed_out.is_set():
                    timed_out.set()
                try:
                    subprocess.run(
                        [DOCKER_CMD, "kill", container_name],
                        capture_output=True, timeout=15,
                    )
                except Exception:
                    pass
            finally:
                timer.cancel()

            end_time  = time.monotonic()
            ended_at  = datetime.now(timezone.utc).isoformat()
            duration_ms = int((end_time - start_time) * 1000)

            # ── docker logs — bounded read ──
            #
            # We open docker logs as a subprocess and read at most
            # stdout_limit / stderr_limit bytes from each stream.
            # This bounds controller memory consumption regardless of how
            # much the sandbox process printed.
            stdout_raw = b""
            stderr_raw = b""
            stdout_truncated = False
            stderr_truncated = False
            try:
                log_proc = subprocess.Popen(
                    [DOCKER_CMD, "logs", container_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                # Bounded read — discards bytes beyond the limit
                (
                    stdout_raw,
                    stdout_truncated,
                    stderr_raw,
                    stderr_truncated,
                ) = self._read_logs_bounded(
                    log_proc,
                    policy.stdout_limit,
                    policy.stderr_limit,
                )
            except Exception:
                pass

            stdout = stdout_raw.decode("utf-8", errors="replace")
            stderr = stderr_raw.decode("utf-8", errors="replace")

            # ── docker inspect (before removal) ──
            inspect_data: dict[str, Any] = {}
            try:
                inspect_result = subprocess.run(
                    [DOCKER_CMD, "inspect", container_name],
                    capture_output=True, text=True, timeout=15,
                )
                if inspect_result.returncode == 0:
                    inspect_data = json.loads(inspect_result.stdout)[0]
            except Exception:
                pass

            oom_killed = False
            try:
                oom_killed = bool(
                    inspect_data.get("State", {}).get("OOMKilled", False)
                )
            except Exception:
                pass

            # Artifacts are in the host output_dir (bind-mount survived exit).
            artifacts = self._validate_artifacts(output_dir)

            # ── docker rm ──
            guard.remove()
            cleanup_verified = self._verify_container_removed(container_name)

            return {
                "exit_code":        exit_code,
                "timed_out":        timed_out.is_set(),
                "oom_killed":       oom_killed,
                "started_at":       started_at,
                "ended_at":         ended_at,
                "duration_ms":      duration_ms,
                "stdout":           stdout,
                "stderr":           stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "inspect":          inspect_data,
                "artifacts":        artifacts,
                "cleanup_verified": cleanup_verified,
            }

    # ── Bounded log reader ────────────────────────────────────────────────

    @staticmethod
    def _read_logs_bounded(
        proc: "subprocess.Popen[bytes]",
        stdout_limit: int,
        stderr_limit: int,
        read_timeout: float = 15.0,
    ) -> tuple[bytes, bool, bytes, bool]:
        """
        Read docker logs output, bounded to stdout_limit / stderr_limit bytes.

        Uses background threads to drain both pipes simultaneously so
        neither blocks.  Bytes beyond the limit are read and discarded so
        the daemon pipe buffer does not fill up and stall the container.
        Joins within read_timeout seconds then kills the logs process.

        Returns:
            (stdout_bytes, stdout_truncated, stderr_bytes, stderr_truncated)
        """
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_total = [0]
        stderr_total = [0]
        stdout_truncated = [False]
        stderr_truncated = [False]

        def _drain(stream, chunks, limit, total, trunc_flag):
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    remaining = limit - total[0]
                    if remaining > 0:
                        take = chunk[:remaining]
                        chunks.append(take)
                        total[0] += len(take)
                        if len(chunk) > remaining:
                            trunc_flag[0] = True
                    else:
                        trunc_flag[0] = True
                    # Bytes beyond limit are discarded (pipe is drained)
            except Exception:
                pass

        t_out = threading.Thread(
            target=_drain,
            args=(proc.stdout, stdout_chunks, stdout_limit, stdout_total, stdout_truncated),
            daemon=True,
        )
        t_err = threading.Thread(
            target=_drain,
            args=(proc.stderr, stderr_chunks, stderr_limit, stderr_total, stderr_truncated),
            daemon=True,
        )
        t_out.start()
        t_err.start()
        t_out.join(timeout=read_timeout)
        t_err.join(timeout=read_timeout)

        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

        return (
            b"".join(stdout_chunks),
            stdout_truncated[0],
            b"".join(stderr_chunks),
            stderr_truncated[0],
        )

    # ── Artifact streaming from tmpfs ────────────────────────────────────

    def _extract_artifacts_stream(
        self, container_name: str, output_dir: Path
    ) -> None:
        """
        Safely stream artifacts from container's /out tmpfs to host output_dir.

        Uses 'docker exec <container> tar -cf - -C /out .' piped into Python's
        tarfile module. Rejects symlinks and paths escaping output_dir.
        """
        try:
            proc = subprocess.Popen(
                [DOCKER_CMD, "exec", container_name, "tar", "-cf", "-", "-C", "/out", "."],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            max_bytes = self.policy.output_limit + 1024 * 1024
            chunks: list[bytes] = []
            total_read = 0
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total_read += len(chunk)
                if total_read > max_bytes:
                    proc.kill()
                    break
            proc.wait(timeout=5)
            tar_data = b"".join(chunks)
            if not tar_data:
                return

            import io
            import tarfile

            output_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r:*") as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(output_dir, filter="data")
                else:
                    for member in tar.getmembers():
                        if not member.isreg():
                            continue
                        target_path = (output_dir / member.name).resolve()
                        if _safe_in_directory(target_path, output_dir):
                            tar.extract(member, output_dir)
        except Exception:
            pass

    # ── Artifact validation ───────────────────────────────────────────────

    def _validate_artifacts(self, output_dir: Path) -> list[dict]:
        """
        Validate, hash, and catalogue artifacts in the output directory.

        Security:
        - Symlinks are rejected unconditionally.
        - Path traversal is blocked using Path.is_relative_to() (not
          str.startswith, which is broken on case-insensitive FS or paths
          that share a prefix without a separator, e.g. /out2 vs /out).
        - Only allow-listed extensions are accepted.
        - Cumulative size is capped at output_limit bytes.
        - PNG files must have valid magic bytes.
        - CSV files must be parseable.
        """
        artifacts: list[dict] = []
        total_size = 0
        output_resolved = output_dir.resolve()

        if not output_dir.exists():
            return artifacts

        for item in sorted(output_dir.iterdir()):
            # Reject symlinks (PRD §6, FR-06)
            if item.is_symlink():
                continue

            if not item.is_file():
                continue

            # Path traversal defence using is_relative_to (separator-aware)
            if not _safe_in_directory(item, output_dir):
                continue

            # Extension allowlist
            ext = item.suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue

            # Cumulative size cap (post-run enforcement)
            try:
                size = item.stat().st_size
            except OSError:
                continue
            total_size += size
            if total_size > self.policy.output_limit:
                break

            # Content validation
            if ext == ".png" and not validate_png(item):
                continue
            if ext == ".csv" and not validate_csv_artifact(item):
                continue

            file_hash = sha256_file(item)
            artifacts.append({
                "name":      item.name,
                "size":      size,
                "sha256":    file_hash,
                "mime_type": MIME_TYPES.get(ext, "application/octet-stream"),
            })

        return artifacts

    # ── Image helpers ─────────────────────────────────────────────────────

    def _get_image_digest(self) -> str:
        """
        Return the immutable image digest (sha256:...) for the sandbox image.

        Prefers the repo digest (set only for images pulled from a registry).
        Falls back to the local image ID which is also content-addressable.
        Records 'unknown' if Docker is unreachable.
        """
        # Repo digest — available for pulled images
        try:
            r = subprocess.run(
                [DOCKER_CMD, "inspect", "--format",
                 "{{index .RepoDigests 0}}", self.image_name],
                capture_output=True, text=True, timeout=15,
            )
            digest = r.stdout.strip()
            if r.returncode == 0 and digest:
                return digest
        except Exception:
            pass

        # Local image ID — content-addressable, always available for local builds
        try:
            r = subprocess.run(
                [DOCKER_CMD, "inspect", "--format", "{{.Id}}", self.image_name],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass

        return "unknown"

    def _verify_container_removed(self, container_name: str) -> bool:
        """Verify the container no longer exists after cleanup."""
        try:
            r = subprocess.run(
                [DOCKER_CMD, "inspect", container_name],
                capture_output=True, timeout=10,
            )
            return r.returncode != 0
        except Exception:
            return False

    # ── Receipt ───────────────────────────────────────────────────────────

    def _build_receipt(
        self,
        task_id: str,
        attempt_id: str,
        container_name: str,
        code: str,
        input_hash: str,
        image_digest: str,
        result: dict,
    ) -> dict:
        """
        Assemble a structured execution receipt from trusted evidence only.

        The model never touches this method.  All values come from the
        controller's own observations or the Docker daemon.
        """
        if result["timed_out"]:
            status = "timeout"
        elif result["oom_killed"]:
            status = "oom_killed"
        elif result["exit_code"] == 0:
            status = "completed"
        else:
            status = "failed"

        receipt: dict[str, Any] = {
            "receipt_version": "1.0",
            "task_id":         task_id,
            "attempt_id":      attempt_id,
            "container_name":  container_name,
            "timestamps": {
                "started_at":  result["started_at"],
                "ended_at":    result["ended_at"],
                "duration_ms": result["duration_ms"],
            },
            "code":       code,
            "input_hash": input_hash,
            "execution": {
                "exit_code":  result["exit_code"],
                "timed_out":  result["timed_out"],
                "oom_killed": result["oom_killed"],
                "status":     status,
            },
            "output": {
                "stdout":           result["stdout"],
                "stderr":           result["stderr"],
                "stdout_truncated": result["stdout_truncated"],
                "stderr_truncated": result["stderr_truncated"],
            },
            "sandbox_policy": {
                "network":           self.policy.network,
                "read_only_root":    self.policy.read_only_root,
                "user":              self.policy.user,
                "cap_drop":          list(self.policy.cap_drop),
                "no_new_privileges": self.policy.no_new_privileges,
                "memory_limit_mb":   self.policy.memory_mb,
                "memory_swap_limit_mb": self.policy.memory_swap_mb,
                "cpu_limit":         self.policy.cpu_limit,
                "pids_limit":        self.policy.pids_limit,
                "tmpfs": {
                    "/tmp": (
                        f"rw,noexec,nosuid,"
                        f"size={self.policy.tmpfs_tmp_size_mb}m,"
                        f"uid={self.policy.user.split(':')[0]},"
                        f"gid={self.policy.user.split(':')[-1]}"
                    ),
                    "/out": (
                        f"rw,noexec,nosuid,"
                        f"size={max(1, self.policy.output_limit // (1024 * 1024))}m,"
                        f"uid={self.policy.user.split(':')[0]},"
                        f"gid={self.policy.user.split(':')[-1]}"
                    ),
                },
                "output_mount": (
                    "bounded tmpfs 20 MB (rw,noexec,nosuid); kernel-enforced during execution"
                ),
                "timeout_seconds":    self.policy.timeout_seconds,
                "stdout_limit_bytes": self.policy.stdout_limit,
                "stderr_limit_bytes": self.policy.stderr_limit,
                "output_limit_bytes": self.policy.output_limit,
                "environment_allowlist": [
                    name for name, _ in self.policy.env_allowlist
                ],
            },
            "container": {
                "name":         container_name,
                "image_tag":    self.image_name,
                "image_digest": image_digest,
                "inspect":      result["inspect"],
            },
            "artifacts":        result["artifacts"],
            "cleanup_verified": result["cleanup_verified"],
            "receipt_hash":     None,
        }

        receipt["receipt_hash"] = compute_receipt_hash(receipt)
        return receipt

    # ── Persistence ───────────────────────────────────────────────────────

    def _save_receipt(
        self, receipt: dict, task_id: str, attempt_id: str,
    ) -> Path:
        """Save receipt JSON to disk and return the receipt directory."""
        receipt_dir = self.receipts_dir / f"{task_id}_{attempt_id}"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = receipt_dir / "receipt.json"
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2, ensure_ascii=False)
        return receipt_dir

    def _persist_artifacts(self, output_dir: Path, receipt_dir: Path) -> None:
        """Copy validated artifacts to the receipt directory."""
        if not output_dir.exists():
            return
        artifacts_dest = receipt_dir / "artifacts"
        artifacts_dest.mkdir(exist_ok=True)
        for item in output_dir.iterdir():
            if item.is_file() and not item.is_symlink():
                if item.suffix.lower() in ALLOWED_EXTENSIONS:
                    shutil.copy2(item, artifacts_dest / item.name)

    # ── Staging cleanup ───────────────────────────────────────────────────

    def _cleanup_task_dir(self, task_dir: Path) -> None:
        """Remove the task staging directory (always runs in finally block)."""
        try:
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)
            parent = task_dir.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except Exception:
            pass


# ─── Orphan reaper ────────────────────────────────────────────────────────────


def reap_orphaned_containers() -> list[str]:
    """
    Remove stale AURON containers.

    Uses *both* the AURON label (``auron.managed=true``) AND the name
    prefix (``auron-``) as filters, so we never accidentally remove
    unrelated containers that happen to contain "auron" in their name.

    Note: ``--filter name=`` on Docker does a substring match, not a
    prefix match.  The explicit ``startswith`` check below guards against
    that.
    """
    removed: list[str] = []
    try:
        # Filter by label first (precise) then verify name prefix
        result = subprocess.run(
            [
                DOCKER_CMD, "ps", "-a",
                "--filter", "label=auron.managed=true",
                "--format", "{{.Names}}",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return removed
        for name in result.stdout.strip().splitlines():
            name = name.strip()
            # Double-check: must also start with our prefix
            if name and name.startswith(CONTAINER_PREFIX):
                subprocess.run(
                    [DOCKER_CMD, "rm", "-f", name],
                    capture_output=True, timeout=15,
                )
                removed.append(name)
    except Exception:
        pass
    return removed


# ─── Image builder ────────────────────────────────────────────────────────────


def build_image(
    tag: str = "latest",
    dockerfile_dir: Path | None = None,
) -> str:
    """Build the AURON sandbox Docker image and return the image name."""
    if dockerfile_dir is None:
        dockerfile_dir = Path(__file__).resolve().parent
    image_name = f"{SANDBOX_IMAGE}:{tag}"
    result = subprocess.run(
        [DOCKER_CMD, "build", "-t", image_name, str(dockerfile_dir)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Image build failed:\n{result.stderr}")
    return image_name


# ─── CLI entry point ─────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AURON Sandbox Runner — Secure Python execution",
        epilog=(
            "Security controls (network, memory, CPU, PIDs, timeout) are "
            "fixed by the controller and cannot be overridden from the CLI."
        ),
    )
    parser.add_argument("csv_path",    type=Path, help="Path to input CSV file")
    parser.add_argument("script_path", type=Path, help="Path to Python script")
    parser.add_argument(
        "--image-tag", default="latest",
        help="Sandbox image tag (default: latest)",
    )
    parser.add_argument(
        "--build", action="store_true",
        help="Build sandbox image before running",
    )
    parser.add_argument(
        "--reap", action="store_true",
        help="Remove orphaned AURON containers before running",
    )
    # No flags for memory / CPU / PIDs / timeout / network —
    # security-critical limits are fixed by the controller.

    args = parser.parse_args()

    if args.reap:
        print("[AURON] Reaping orphaned containers...", file=sys.stderr)
        removed = reap_orphaned_containers()
        if removed:
            print(
                f"[AURON] Removed {len(removed)} orphan(s): " + ", ".join(removed),
                file=sys.stderr,
            )

    if args.build:
        print("[AURON] Building sandbox image...", file=sys.stderr)
        build_image(tag=args.image_tag)

    runner = SandboxRunner(image_tag=args.image_tag)
    print("[AURON] Starting sandbox execution...", file=sys.stderr)
    receipt = runner.run(args.csv_path, args.script_path)

    print(json.dumps(receipt, indent=2))

    status   = receipt["execution"]["status"]
    duration = receipt["timestamps"]["duration_ms"]
    n_art    = len(receipt["artifacts"])
    print(
        f"\n[AURON] Status: {status} | Duration: {duration}ms | Artifacts: {n_art}",
        file=sys.stderr,
    )
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
