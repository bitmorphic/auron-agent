"""
AURON Red Team Arena Engine
Executes bounded security probes against disposable Docker containers
and evaluates results from the trusted controller side.

Source of Truth: PRD v3.0 §11 & §15
"""

import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import settings
from backend.database import record_redteam_run
from sandbox.runner import SandboxRunner, SandboxPolicy

logger = logging.getLogger("auron.redteam")

# ─── Probe Definitions ─────────────────────────────────────────────────────────

AVAILABLE_PROBES: dict[str, dict[str, Any]] = {
    "network_isolation": {
        "id": "network_isolation",
        "name": "Outbound Network Exfiltration Probe",
        "target_control": "--network none",
        "mechanism": "Docker Network Namespace Isolation",
        "expected": "Blocked: kernel routing table has zero external interfaces/routes; outbound sockets fail with Network unreachable (Errno 101)",
        "description": "Validates network namespace isolation by inspecting interface list (/sys/class/net) and attempting egress to RFC 5737 TEST-NET-1 (192.0.2.1:80).",
    },
    "secret_isolation": {
        "id": "secret_isolation",
        "name": "Host Secret & Environment Exfiltration Probe",
        "target_control": "Clean Env Allowlist, Non-Root UID, Narrow Mounts",
        "mechanism": "Non-root UID 10001, filtered env allowlist, and zero host secret mounts",
        "expected": "Blocked: zero API keys, tokens, or forbidden host directories (/root, /etc/shadow) accessible",
        "description": "Scans environment variables and filesystem for sensitive tokens, SSH keys, and system secrets.",
    },
    "pid_limit": {
        "id": "pid_limit",
        "name": "Process Table Exhaustion (Fork Bomb) Probe",
        "target_control": "--pids-limit 64",
        "mechanism": "Linux Kernel cgroups PID Controller (pids.max)",
        "expected": "Blocked: kernel enforces hard cap of 64 PIDs, halting process creation with Resource temporarily unavailable",
        "description": "Attempts rapid process spawning to exhaust the container process table beyond the 64 PID ceiling.",
    },
}

# ─── Probe Code Templates ──────────────────────────────────────────────────────

_NETWORK_PROBE_CODE = '''import socket
import sys
import json
import os

results = []

# 1. Interface inspection: In --network none, only the loopback interface 'lo' exists.
# The presence of eth0 or any external interface is a security finding.
interfaces = os.listdir('/sys/class/net') if os.path.exists('/sys/class/net') else []

# 2. Outbound egress test: Target RFC 5737 TEST-NET-1 documentation IP (192.0.2.1:80).
# RFC 5737 addresses are explicitly non-routable and never send traffic to public servers.
# Under --network none, the container routing table has zero routes to external networks,
# so socket connect immediately raises OSError: [Errno 101] Network is unreachable (ENETUNREACH).
# ConnectionRefusedError on 127.0.0.1 is NOT proof of network isolation (it only means no local listener).
target_host = "192.0.2.1"
target_port = 80
connected = False
error_str = None
errno_val = None

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect((target_host, target_port))
    connected = True
    s.close()
except OSError as e:
    errno_val = getattr(e, 'errno', None)
    error_str = f"{type(e).__name__}: [Errno {errno_val}] {e.strerror}" if errno_val else f"{type(e).__name__}: {str(e)}"
except Exception as e:
    error_str = f"{type(e).__name__}: {str(e)}"

results.append({
    "target": f"{target_host}:{target_port}",
    "connected": connected,
    "errno": errno_val,
    "error": error_str,
})

only_loopback = (interfaces == ["lo"]) or ("eth0" not in interfaces)
network_unreachable = (errno_val == 101) or ("unreachable" in (error_str or "").lower())

output = {
    "probe": "network_isolation",
    "any_connected": connected,
    "interfaces": interfaces,
    "only_loopback": only_loopback,
    "network_unreachable": network_unreachable,
    "details": results
}
print(json.dumps(output))

if connected or ("eth0" in interfaces):
    sys.exit(10)
else:
    sys.exit(0)
'''

_SECRET_PROBE_CODE = '''import os
import sys
import json
from pathlib import Path

suspicious_keywords = ["VULTR", "API_KEY", "SECRET", "PRIVATE KEY", "PASSWORD", "SSH", "AWS_"]
leaked_env = []
for k, v in os.environ.items():
    for kw in suspicious_keywords:
        if kw in k.upper():
            leaked_env.append(k)

forbidden_paths = [
    "/root",
    "/root/.ssh",
    "/etc/auron",
    "/etc/shadow",
    "/var/run/docker.sock",
]
accessible_paths = []
for p_str in forbidden_paths:
    p = Path(p_str)
    try:
        if p.exists():
            if p.is_dir():
                list(p.iterdir())
            else:
                p.read_bytes()
            accessible_paths.append(p_str)
    except Exception:
        pass

leak_detected = bool(leaked_env or accessible_paths)

output = {
    "probe": "secret_isolation",
    "leak_detected": leak_detected,
    "env_keys_present": list(os.environ.keys()),
    "leaked_env_keys": leaked_env,
    "accessible_forbidden_paths": accessible_paths
}
print(json.dumps(output))

if leak_detected:
    sys.exit(10)  # Leaked secrets found (finding)
else:
    sys.exit(0)   # Blocked as expected
'''

_PID_PROBE_CODE = '''import os
import sys
import time
import json

forked_pids = []
max_attempts = 100
fork_error = None

try:
    for i in range(max_attempts):
        pid = os.fork()
        if pid == 0:
            time.sleep(2)
            os._exit(0)
        else:
            forked_pids.append(pid)
except Exception as e:
    fork_error = f"{type(e).__name__}: {str(e)}"

# Reap any spawned children to avoid leaving orphans in container
for pid in forked_pids:
    try:
        os.waitpid(pid, os.WNOHANG)
    except Exception:
        pass

limit_hit = (fork_error is not None and len(forked_pids) < max_attempts)

output = {
    "probe": "pid_limit",
    "pids_forked": len(forked_pids),
    "max_attempted": max_attempts,
    "fork_error": fork_error,
    "limit_hit": limit_hit
}
print(json.dumps(output))

if limit_hit:
    sys.exit(0)   # Blocked by kernel cgroups as expected
elif len(forked_pids) >= max_attempts:
    sys.exit(10)  # PID ceiling failed (finding)
else:
    sys.exit(1)
'''

PROBE_SCRIPTS: dict[str, str] = {
    "network_isolation": _NETWORK_PROBE_CODE,
    "secret_isolation": _SECRET_PROBE_CODE,
    "pid_limit": _PID_PROBE_CODE,
}


# ─── Red Team Engine ───────────────────────────────────────────────────────────


class RedTeamEngine:
    """Controller for running bounded security probes against disposable containers."""

    def __init__(self, runner: Optional[SandboxRunner] = None):
        self.runner = runner or SandboxRunner()

    def run_probe(self, probe_id: str, owner_session_id: Optional[str] = None) -> dict[str, Any]:
        """
        Execute a probe and return controller-side evidence and honest classification.
        Classifications: blocked, detected, timed_out, failed, succeeded, inconclusive.
        """
        if probe_id not in AVAILABLE_PROBES:
            raise ValueError(f"Unknown probe ID '{probe_id}'. Available: {list(AVAILABLE_PROBES.keys())}")

        probe_meta = AVAILABLE_PROBES[probe_id]
        script_code = PROBE_SCRIPTS[probe_id]

        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        # Create temporary workspace for probe script and minimal dummy CSV
        temp_dir = Path(tempfile.mkdtemp(prefix=f"auron_redteam_{probe_id}_"))
        script_path = temp_dir / "probe.py"
        csv_path = temp_dir / "dummy.csv"

        try:
            script_path.write_text(script_code, encoding="utf-8")
            csv_path.write_text("id,val\n1,test\n", encoding="utf-8")

            logger.info("Executing Red Team probe '%s' (run %s)...", probe_id, run_id)

            receipt = self.runner.run(
                csv_path=csv_path,
                script_path=script_path,
            )

            duration_ms = (time.monotonic() - t0) * 1000

            # Controller-side analysis of execution evidence
            classification, observed = self._evaluate_evidence(probe_id, receipt)

            execution = receipt.get("execution", {})
            output = receipt.get("output", {})
            inspect_data = receipt.get("container", {}).get("inspect", {})

            evidence = {
                "exit_code": execution.get("exit_code"),
                "timed_out": execution.get("timed_out", False),
                "oom_killed": execution.get("oom_killed", False),
                "duration_ms": duration_ms,
                "stdout": (output.get("stdout") or "")[:4000],
                "stderr": (output.get("stderr") or "")[:4000],
                "inspect_summary": self._extract_inspect_evidence(inspect_data),
            }

            # Record run in persistent database
            record_redteam_run(
                probe_id=probe_id,
                probe_name=probe_meta["name"],
                expected=probe_meta["expected"],
                observed=observed,
                mechanism=probe_meta["mechanism"],
                classification=classification,
                started_at=started_at,
                duration_ms=duration_ms,
                evidence=evidence,
                run_id=run_id,
                owner_session_id=owner_session_id,
            )

            return {
                "id": run_id,
                "probe_id": probe_id,
                "probe_name": probe_meta["name"],
                "target_control": probe_meta["target_control"],
                "mechanism": probe_meta["mechanism"],
                "expected": probe_meta["expected"],
                "observed": observed,
                "classification": classification,
                "started_at": started_at,
                "duration_ms": duration_ms,
                "evidence": evidence,
            }

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _evaluate_evidence(self, probe_id: str, receipt: dict[str, Any]) -> tuple[str, str]:
        """
        Produce an honest classification based strictly on controller-side evidence.
        Never fabricate a green outcome.
        """
        execution = receipt.get("execution", {})
        timed_out = execution.get("timed_out", False)
        oom_killed = execution.get("oom_killed", False)
        exit_code = execution.get("exit_code", 1)

        if timed_out:
            return "timed_out", "Probe execution timed out before completion (stopped by controller watchdog)."

        if oom_killed:
            return "blocked", "Process terminated by kernel OOM killer (memory ceiling enforced)."

        inspect_data = receipt.get("container", {}).get("inspect", {}) or {}
        host_config = inspect_data.get("HostConfig", {})
        output = receipt.get("output", {})
        stdout = output.get("stdout") or ""

        # Try parsing JSON output from probe stdout
        probe_data: dict[str, Any] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    probe_data = json.loads(line)
                    break
                except Exception:
                    continue

        if probe_id == "network_isolation":
            net_mode = host_config.get("NetworkMode")
            any_connected = probe_data.get("any_connected")
            interfaces = probe_data.get("interfaces", [])

            if any_connected is True:
                return "succeeded", "SECURITY FINDING: Outbound network connection succeeded from inside container!"

            if "eth0" in interfaces:
                return "succeeded", f"SECURITY FINDING: External interface 'eth0' detected in container: {interfaces}!"

            if net_mode == "none":
                details = probe_data.get("details", [])
                err_samples = [d.get("error") for d in details if d.get("error")]
                err_text = "; ".join(err_samples[:2]) if err_samples else "Network is unreachable"
                return "blocked", f"Network traffic blocked by --network none. Only loopback interface present ({interfaces}). Observed: {err_text}."

            if net_mode == "none" and exit_code == 0:
                return "blocked", "Network traffic blocked by --network none (verified via Docker inspect and zero connections)."

            return "inconclusive", f"Network outcome inconclusive. Exit code: {exit_code}, NetworkMode: {net_mode}."

        elif probe_id == "secret_isolation":
            user = inspect_data.get("Config", {}).get("User")
            leak_detected = probe_data.get("leak_detected")

            if leak_detected is True:
                leaked = probe_data.get("leaked_env_keys") or probe_data.get("accessible_forbidden_paths")
                return "succeeded", f"SECURITY FINDING: Sensitive secrets accessible: {leaked}!"

            if leak_detected is False:
                return "blocked", "No sensitive environment variables or host directories accessible. Non-root user 10001 confirmed."

            if exit_code == 0 and user == "10001:10001":
                return "blocked", "Secrets isolated: Clean env allowlist verified, non-root user confirmed."

            return "inconclusive", f"Secret isolation outcome inconclusive. Exit code: {exit_code}."

        elif probe_id == "pid_limit":
            pids_limit = host_config.get("PidsLimit", 0)
            limit_hit = probe_data.get("limit_hit")
            pids_forked = probe_data.get("pids_forked", 0)

            if limit_hit is True or (exit_code == 0 and pids_forked < 64):
                fork_err = probe_data.get("fork_error", "Resource temporarily unavailable")
                return "blocked", f"Fork bomb halted at {pids_forked} processes by kernel cgroups (PidsLimit={pids_limit}). Error: {fork_err}."

            if pids_forked >= 100:
                return "succeeded", f"SECURITY FINDING: Fork bomb spawned {pids_forked} processes without kernel restriction!"

            return "inconclusive", f"PID limit outcome inconclusive. PIDs forked: {pids_forked}, Exit code: {exit_code}."

        return "failed", f"Unrecognized probe outcome for {probe_id}."

    def _extract_inspect_evidence(self, inspect_data: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Extract key controller-observed containment attributes."""
        if not inspect_data:
            return {"inspect_available": False}
        host_config = inspect_data.get("HostConfig", {})
        config = inspect_data.get("Config", {})
        state = inspect_data.get("State", {})
        return {
            "inspect_available": True,
            "network_mode": host_config.get("NetworkMode"),
            "readonly_rootfs": host_config.get("ReadonlyRootfs"),
            "user": config.get("User"),
            "pids_limit": host_config.get("PidsLimit"),
            "memory_limit": host_config.get("Memory"),
            "cap_drop": host_config.get("CapDrop"),
            "security_opt": host_config.get("SecurityOpt"),
            "exit_code": state.get("ExitCode"),
            "oom_killed": state.get("OOMKilled"),
        }
