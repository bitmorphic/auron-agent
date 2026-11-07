"""
Tests for AURON Red Team Arena
Verifies probe definitions, controller evaluation logic, authentication,
honest classification, and API endpoints.

Source of Truth: PRD v3.0 §11 & §15
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.database import init_db
from backend.main import app
from backend.redteam.engine import RedTeamEngine, AVAILABLE_PROBES


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    test_db = tmp_path / "test_redteam.db"
    init_db(test_db)
    with patch.object(settings, "db_path", test_db), \
         patch("backend.main.reap_orphaned_containers", return_value=[]):
        yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_redteam_probes_listing(client: TestClient):
    """Verify all 3 required probes are registered with complete metadata."""
    resp = client.get("/api/redteam/probes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    probe_ids = {p["id"] for p in data["probes"]}
    assert "network_isolation" in probe_ids
    assert "secret_isolation" in probe_ids
    assert "pid_limit" in probe_ids


def test_redteam_auth_enforcement(client: TestClient):
    """Verify unauthorized requests are rejected."""
    # Missing token
    resp = client.post("/api/redteam/network_isolation/run")
    assert resp.status_code == 401

    # Invalid token
    resp = client.post(
        "/api/redteam/network_isolation/run",
        headers={"X-RedTeam-Token": "wrong-token"},
    )
    assert resp.status_code == 401

    # Runs endpoint with invalid token
    resp = client.get("/api/redteam/runs", headers={"X-RedTeam-Token": "wrong-token"})
    assert resp.status_code == 401


def test_redteam_disabled_feature_flag(client: TestClient):
    """Verify HTTP 403 Forbidden when RED_TEAM_ENABLED is False."""
    with patch.object(settings, "red_team_enabled", False):
        resp = client.post(
            "/api/redteam/network_isolation/run",
            headers={"X-RedTeam-Token": settings.red_team_token},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()


def test_network_isolation_blocked_classification():
    """Verify network isolation probe classifies as 'blocked' when network is none."""
    mock_runner = MagicMock()
    mock_runner.run.return_value = {
        "execution": {
            "exit_code": 0,
            "timed_out": False,
            "oom_killed": False,
            "status": "completed",
        },
        "output": {
            "stdout": json.dumps({
                "probe": "network_isolation",
                "any_connected": False,
                "details": [
                    {"target": "1.1.1.1:80", "connected": False, "error": "OSError: [Errno 101] Network is unreachable"},
                    {"target": "8.8.8.8:53", "connected": False, "error": "OSError: [Errno 101] Network is unreachable"},
                ]
            }),
            "stderr": "",
        },
        "timestamps": {"duration_ms": 120.0},
        "container": {
            "inspect": {
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True},
                "Config": {"User": "10001:10001"},
                "State": {"ExitCode": 0, "OOMKilled": False},
            }
        },
    }

    engine = RedTeamEngine(runner=mock_runner)
    result = engine.run_probe("network_isolation")

    assert result["classification"] == "blocked"
    assert "network is unreachable" in result["observed"].lower()
    assert result["evidence"]["exit_code"] == 0
    assert result["evidence"]["inspect_summary"]["network_mode"] == "none"


def test_network_isolation_honest_finding_on_leak():
    """Verify that if network connection succeeds, it is classified as 'succeeded' finding, NOT blocked."""
    mock_runner = MagicMock()
    mock_runner.run.return_value = {
        "execution": {
            "exit_code": 10,
            "timed_out": False,
            "oom_killed": False,
            "status": "failed",
        },
        "output": {
            "stdout": json.dumps({
                "probe": "network_isolation",
                "any_connected": True,
                "details": [
                    {"target": "1.1.1.1:80", "connected": True, "error": None},
                ]
            }),
            "stderr": "",
        },
        "timestamps": {"duration_ms": 150.0},
        "container": {
            "inspect": {"HostConfig": {"NetworkMode": "bridge"}}
        },
    }

    engine = RedTeamEngine(runner=mock_runner)
    result = engine.run_probe("network_isolation")

    # MUST be honest — finding reported!
    assert result["classification"] == "succeeded"
    assert "SECURITY FINDING" in result["observed"]


def test_secret_isolation_blocked_classification():
    """Verify secret isolation classifies as 'blocked' when no secrets are leaked."""
    mock_runner = MagicMock()
    mock_runner.run.return_value = {
        "execution": {
            "exit_code": 0,
            "timed_out": False,
            "oom_killed": False,
            "status": "completed",
        },
        "output": {
            "stdout": json.dumps({
                "probe": "secret_isolation",
                "leak_detected": False,
                "env_keys_present": ["PATH", "MPLBACKEND", "MPLCONFIGDIR"],
                "leaked_env_keys": [],
                "accessible_forbidden_paths": []
            }),
            "stderr": "",
        },
        "timestamps": {"duration_ms": 90.0},
        "container": {
            "inspect": {
                "HostConfig": {"ReadonlyRootfs": True},
                "Config": {"User": "10001:10001"},
            }
        },
    }

    engine = RedTeamEngine(runner=mock_runner)
    result = engine.run_probe("secret_isolation")

    assert result["classification"] == "blocked"
    assert "no sensitive" in result["observed"].lower()


def test_pid_limit_blocked_classification():
    """Verify pid limit classifies as 'blocked' when fork bomb is stopped by cgroups."""
    mock_runner = MagicMock()
    mock_runner.run.return_value = {
        "execution": {
            "exit_code": 0,
            "timed_out": False,
            "oom_killed": False,
            "status": "completed",
        },
        "output": {
            "stdout": json.dumps({
                "probe": "pid_limit",
                "pids_forked": 61,
                "max_attempted": 100,
                "fork_error": "BlockingIOError: [Errno 11] Resource temporarily unavailable",
                "limit_hit": True
            }),
            "stderr": "",
        },
        "timestamps": {"duration_ms": 210.0},
        "container": {
            "inspect": {
                "HostConfig": {"PidsLimit": 64},
            }
        },
    }

    engine = RedTeamEngine(runner=mock_runner)
    result = engine.run_probe("pid_limit")

    assert result["classification"] == "blocked"
    assert "halted at 61" in result["observed"]
    assert "pidslimit=64" in result["observed"].lower()


def test_redteam_api_execution_and_persistence(client: TestClient):
    """Verify end-to-end API route with token, persistence in SQLite, and retrieval."""
    with patch("backend.routes.redteam.get_engine") as mock_get_engine:
        mock_engine = MagicMock()
        mock_engine.run_probe.return_value = {
            "id": "test-run-123",
            "probe_id": "network_isolation",
            "probe_name": "Outbound Network Exfiltration Probe",
            "classification": "blocked",
            "observed": "Network traffic blocked by --network none.",
            "started_at": "2026-11-07T12:00:00Z",
            "duration_ms": 100.0,
            "evidence": {"exit_code": 0},
        }
        mock_get_engine.return_value = mock_engine

        # Execute probe via POST
        resp = client.post(
            "/api/redteam/network_isolation/run",
            headers={"X-RedTeam-Token": settings.red_team_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-run-123"
        assert data["classification"] == "blocked"

        # List runs
        runs_resp = client.get(
            "/api/redteam/runs",
            headers={"X-RedTeam-Token": settings.red_team_token},
        )
        assert runs_resp.status_code == 200
        assert isinstance(runs_resp.json(), list)
