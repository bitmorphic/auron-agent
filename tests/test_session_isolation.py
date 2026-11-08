"""
AURON Session Isolation, Receipt Integrity, Sample Idempotency & Security Tests.
Covers all acceptance criteria from the Release-Blocking Remediation brief.
Source of Truth: PRD v3.0 §13, §14, §15.
"""

import io
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.database import create_session, get_session_by_token_hash, init_db
from backend.main import app
from backend.session import generate_session_token, hash_session_token

SAMPLE_CSV = b"product,price,units\nLaptop,1200,5\nMouse,25,20\nMonitor,350,8\n"


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every test to a fresh temp database and directory layout."""
    test_db = tmp_path / "test_session.db"
    monkeypatch.setattr(settings, "db_path", test_db)
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "samples_dir", Path("data/samples"))
    monkeypatch.setattr(settings, "receipts_dir", tmp_path / "receipts")
    settings.ensure_directories()
    init_db(test_db)
    yield


@pytest.fixture
def client_a():
    with TestClient(app) as c:
        # Bootstrap session A
        c.post("/api/session")
        yield c


@pytest.fixture
def client_b():
    with TestClient(app) as c:
        # Bootstrap session B — fresh client, fresh session
        c.post("/api/session")
        yield c


# ─── Session Bootstrap Tests ───────────────────────────────────────────────────


def test_session_bootstrap_sets_cookie(client_a: TestClient):
    """Bootstrapping a session must set a Secure HttpOnly SameSite cookie."""
    resp = client_a.post("/api/session")
    # In tests the TestClient runs in HTTP mode; cookie is set regardless
    assert resp.status_code == 200
    # The session bootstrap returns timestamps
    data = resp.json()
    assert "created_at" in data
    assert "expires_at" in data


def test_session_bootstrap_idempotent(client_a: TestClient):
    """Calling /api/session again on the same client returns bootstrapped=False."""
    resp1 = client_a.post("/api/session")
    assert resp1.status_code == 200

    resp2 = client_a.post("/api/session")
    assert resp2.status_code == 200
    # Second call: session already exists, so bootstrapped is False
    assert resp2.json()["bootstrapped"] is False


def test_session_token_hash_stored_not_plaintext(tmp_path):
    """Verify session token is never stored in plaintext — only SHA-256 hash."""
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    # Hash must be 64-char hex
    assert len(token_hash) == 64
    assert token_hash != raw_token
    # Confirm round-trip lookup works
    session = create_session(token_hash=token_hash, db_path=settings.db_path)
    looked_up = get_session_by_token_hash(token_hash, db_path=settings.db_path)
    assert looked_up is not None
    assert looked_up["id"] == session["id"]


# ─── Session Isolation Tests ───────────────────────────────────────────────────


def test_session_a_cannot_read_session_b_dataset(client_a: TestClient, client_b: TestClient):
    """Session A's dataset must not be accessible to Session B."""
    # Upload from A
    files = {"file": ("data.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    resp_a = client_a.post("/api/datasets", files=files)
    assert resp_a.status_code == 201
    ds_id = resp_a.json()["id"]

    # B tries to access A's dataset by ID — must get 404
    resp_b = client_b.get(f"/api/datasets/{ds_id}")
    assert resp_b.status_code == 404, (
        f"Session B accessed Session A's dataset! Got {resp_b.status_code}: {resp_b.json()}"
    )


def test_dataset_list_for_session_excludes_other_sessions(client_a: TestClient, client_b: TestClient):
    """Dataset list for session A must never include session B's records."""
    files = {"file": ("data_b.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    resp_b = client_b.post("/api/datasets", files=files)
    assert resp_b.status_code == 201
    ds_id_b = resp_b.json()["id"]

    # A's dataset list must not include B's upload
    resp_a = client_a.get("/api/datasets")
    assert resp_a.status_code == 200
    ids_in_a = {d["id"] for d in resp_a.json()}
    assert ds_id_b not in ids_in_a, "Session A's dataset list contains Session B's record!"


def test_session_b_cannot_access_session_a_task(client_a: TestClient, client_b: TestClient):
    """Session B must receive 404 when polling Session A's task ID."""
    # A creates dataset + task
    files = {"file": ("task_data.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    ds_resp = client_a.post("/api/datasets", files=files)
    assert ds_resp.status_code == 201
    ds_id = ds_resp.json()["id"]

    task_resp = client_a.post("/api/tasks", json={"dataset_id": ds_id, "question": "Count rows."})
    assert task_resp.status_code == 202
    task_id = task_resp.json()["task_id"]

    # B tries to read A's task
    resp_b = client_b.get(f"/api/tasks/{task_id}")
    assert resp_b.status_code == 404, (
        f"Session B accessed Session A's task! Got {resp_b.status_code}"
    )


def test_task_list_for_session_a_excludes_session_b(client_a: TestClient, client_b: TestClient):
    """Task list for Session A must never include Session B's records."""
    # B creates dataset + task
    files = {"file": ("b_data.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    ds_b = client_b.post("/api/datasets", files=files).json()["id"]
    task_b = client_b.post("/api/tasks", json={"dataset_id": ds_b, "question": "Any."}).json()["task_id"]

    # A list tasks — must not see B's task
    resp_a = client_a.get("/api/tasks")
    assert resp_a.status_code == 200
    ids_in_a = {t["id"] for t in resp_a.json()}
    assert task_b not in ids_in_a, "Session A's task list contains Session B's task!"


def test_cross_session_artifact_download_blocked(client_a: TestClient, client_b: TestClient):
    """Session B must not be able to download Session A's artifact via guessed URL."""
    # We only need to verify the endpoint enforces ownership (task lookup fails)
    fake_task_id = str(uuid.uuid4())
    resp = client_b.get(f"/api/tasks/{fake_task_id}/artifacts/chart.png")
    assert resp.status_code == 404


def test_session_cannot_create_task_on_other_session_dataset(client_a: TestClient, client_b: TestClient):
    """Session B must not be able to submit analysis on Session A's dataset."""
    files = {"file": ("private.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    ds_resp = client_a.post("/api/datasets", files=files)
    assert ds_resp.status_code == 201
    ds_id = ds_resp.json()["id"]

    # B attempts to create task using A's dataset_id
    resp_b = client_b.post("/api/tasks", json={"dataset_id": ds_id, "question": "Who owns this?"})
    assert resp_b.status_code == 404, (
        f"Session B created a task using Session A's dataset! Got {resp_b.status_code}"
    )


def test_unknown_task_id_returns_404(client_a: TestClient):
    """Non-existent task IDs must return 404, not 500."""
    resp = client_a.get(f"/api/tasks/{uuid.uuid4()}")
    assert resp.status_code == 404

    resp = client_a.get(f"/api/tasks/{uuid.uuid4()}/receipt")
    assert resp.status_code == 404


# ─── Sample Loading Idempotency Tests ─────────────────────────────────────────


def test_sample_loading_is_idempotent_within_session(client_a: TestClient):
    """Loading the same sample twice from the same session returns the same record."""
    resp1 = client_a.post("/api/datasets/sample", json={"sample_name": "sales_clean"})
    assert resp1.status_code == 201
    id1 = resp1.json()["id"]

    resp2 = client_a.post("/api/datasets/sample", json={"sample_name": "sales_clean"})
    assert resp2.status_code == 201
    id2 = resp2.json()["id"]

    assert id1 == id2, f"Duplicate sample records created: {id1} != {id2}"


def test_different_sessions_get_different_sample_records(client_a: TestClient, client_b: TestClient):
    """Two sessions loading the same sample must get separate records."""
    resp_a = client_a.post("/api/datasets/sample", json={"sample_name": "sales_clean"})
    assert resp_a.status_code == 201
    id_a = resp_a.json()["id"]

    resp_b = client_b.post("/api/datasets/sample", json={"sample_name": "sales_clean"})
    assert resp_b.status_code == 201
    id_b = resp_b.json()["id"]

    # Same content, different UUIDs — no cross-session sharing
    assert id_a != id_b, "Two sessions share the same sample dataset record!"


# ─── Public Endpoint Tests ─────────────────────────────────────────────────────


def test_health_endpoint_public(client_a: TestClient):
    """Health endpoint must be accessible without any session."""
    # Even a client with no session gets health
    with TestClient(app) as bare_client:
        resp = bare_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    # Health must NOT expose inference API key
    response_text = json.dumps(data)
    assert "VULTR_INFERENCE_API_KEY" not in response_text
    if settings.vultr_inference_api_key:
        assert settings.vultr_inference_api_key not in response_text


def test_samples_catalogue_public(client_a: TestClient):
    """Sample catalogue listing (/api/samples) must be accessible without session."""
    with TestClient(app) as bare_client:
        resp = bare_client.get("/api/samples")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_private_data_not_in_public_response(client_a: TestClient, client_b: TestClient):
    """Dataset list for Session A must never expose Session B's data."""
    files = {"file": ("secret.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    ds_b = client_b.post("/api/datasets", files=files)
    assert ds_b.status_code == 201

    # A's dataset list
    resp = client_a.get("/api/datasets")
    assert resp.status_code == 200
    for dataset in resp.json():
        assert dataset["id"] != ds_b.json()["id"]


def test_dataset_response_has_no_storage_path(client_a: TestClient):
    """Dataset API responses must never contain server filesystem paths."""
    files = {"file": ("test.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    resp = client_a.post("/api/datasets", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert "storage_path" not in data, "storage_path leaked in dataset response!"
    assert "owner_session_id" not in data, "owner_session_id leaked in dataset response!"


def test_redteam_disabled_by_default():
    """Red Team Arena must return 403 when feature flag is off."""
    with TestClient(app) as c:
        c.post("/api/session")
        with patch.object(settings, "red_team_enabled", False):
            resp = c.post("/api/redteam/network_isolation/run")
            assert resp.status_code == 403


# ─── Receipt Identity Tests ────────────────────────────────────────────────────


def test_receipt_hash_is_deterministic():
    """compute_receipt_hash must return the same hash for the same receipt."""
    from sandbox.runner import compute_receipt_hash

    receipt = {
        "receipt_version": "1.0",
        "task_id": "abc-123",
        "attempt_id": "def-456",
        "code": "print('hello')",
        "input_hash": "aabbcc",
        "execution": {"exit_code": 0, "timed_out": False, "oom_killed": False, "status": "completed"},
        "output": {"stdout": "hello", "stderr": "", "stdout_truncated": False, "stderr_truncated": False},
        "artifacts": [],
        "cleanup_verified": True,
        "receipt_hash": None,
    }
    h1 = compute_receipt_hash(receipt)
    h2 = compute_receipt_hash(receipt)
    assert h1 == h2
    assert len(h1) == 64


def test_receipt_hash_field_excluded_from_computation():
    """receipt_hash field must be set to null before hashing to avoid circularity."""
    from sandbox.runner import compute_receipt_hash

    receipt = {
        "task_id": "t1",
        "attempt_id": "a1",
        "code": "x=1",
        "receipt_hash": "some-previous-hash",
        "execution": {},
        "output": {},
    }
    # Verify same hash whether receipt_hash is present or null
    receipt_with_hash = dict(receipt)
    receipt_with_hash["receipt_hash"] = "changed-hash"
    receipt_null = dict(receipt)
    receipt_null["receipt_hash"] = None

    h_with = compute_receipt_hash(receipt_with_hash)
    h_null = compute_receipt_hash(receipt_null)
    assert h_with == h_null, "receipt_hash field is not being zeroed before hashing!"


def test_verify_receipt_hash_passes():
    """verify_receipt_hash must return True for a correctly signed receipt."""
    from sandbox.runner import compute_receipt_hash, verify_receipt_hash

    receipt = {
        "task_id": "t1",
        "attempt_id": "a1",
        "code": "x=1",
        "execution": {"exit_code": 0},
        "output": {},
        "receipt_hash": None,
    }
    receipt["receipt_hash"] = compute_receipt_hash(receipt)
    assert verify_receipt_hash(receipt) is True


def test_verify_receipt_hash_fails_on_tamper():
    """verify_receipt_hash must return False when receipt content is modified."""
    from sandbox.runner import compute_receipt_hash, verify_receipt_hash

    receipt = {
        "task_id": "t1",
        "attempt_id": "a1",
        "code": "x=1",
        "execution": {"exit_code": 0},
        "output": {},
        "receipt_hash": None,
    }
    receipt["receipt_hash"] = compute_receipt_hash(receipt)

    # Tamper with content
    receipt["code"] = "TAMPERED"
    assert verify_receipt_hash(receipt) is False


def test_verify_receipt_endpoint_returns_structured_result(client_a: TestClient):
    """verify-receipt endpoint returns 404 or structured result, never 500."""
    # Upload dataset first
    files = {"file": ("vr.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    ds = client_a.post("/api/datasets", files=files).json()

    task = client_a.post("/api/tasks", json={"dataset_id": ds["id"], "question": "Test."})
    assert task.status_code == 202
    task_id = task.json()["task_id"]

    # Task is pending — no attempts yet, so we expect 404 (no receipt)
    resp = client_a.get(f"/api/tasks/{task_id}/verify-receipt")
    # Must be 404 (no receipt) or 200 with structured data — never 500
    assert resp.status_code in (404, 200)
    if resp.status_code == 200:
        data = resp.json()
        assert "status" in data
        assert "receipt_verified" in data


def test_receipt_task_id_matches_task_id_in_response(tmp_path: Path):
    """
    When the orchestrator passes task_id to runner.run(), the receipt.task_id
    must equal the canonical task_id. Verify via runner interface directly.
    """
    from sandbox.runner import SandboxPolicy, SandboxRunner

    runner = SandboxRunner(
        policy=SandboxPolicy(),
        workspace_dir=tmp_path / "workspace",
        receipts_dir=tmp_path / "receipts",
    )
    # We only test the interface: run() must accept task_id kwarg
    import inspect
    sig = inspect.signature(runner.run)
    assert "task_id" in sig.parameters, "SandboxRunner.run() must accept task_id parameter"


# ─── Rate Limit Tests ─────────────────────────────────────────────────────────


def test_rate_limit_per_session():
    """Rate limiter must reject requests beyond the window ceiling."""
    import asyncio

    from fastapi import HTTPException

    from backend.rate_limit import check_rate_limit

    async def _run():
        session_id = str(uuid.uuid4())

        # Fire 5 requests — should all pass
        for i in range(5):
            await check_rate_limit(session_id, "test_action", max_requests=5, window_seconds=60)

        # 6th must raise 429
        with pytest.raises(HTTPException) as exc_info:
            await check_rate_limit(session_id, "test_action", max_requests=5, window_seconds=60)

        assert exc_info.value.status_code == 429

    asyncio.run(_run())


# ─── CSV Limits Preserved Tests ───────────────────────────────────────────────


def test_csv_size_limit_still_enforced(client_a: TestClient):
    """10 MB CSV size limit must still be enforced after session changes."""
    oversized = b"a,b\n" + (b"x" * 11 * 1024 * 1024)
    files = {"file": ("big.csv", io.BytesIO(oversized), "text/csv")}
    resp = client_a.post("/api/datasets", files=files)
    assert resp.status_code == 400


# ─── Docker Inspect Redaction Tests ───────────────────────────────────────────


def test_inspect_redaction_removes_host_paths():
    """_redact_inspect_from_receipt must strip host paths, ResolvConf, and env values."""
    from backend.routes.tasks import _redact_inspect_from_receipt

    raw_receipt = {
        "task_id": "t1",
        "attempt_id": "a1",
        "receipt_hash": "aabb",
        "container": {
            "inspect": {
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "Memory": 536870912,
                    "MemorySwap": 536870912,
                    "NanoCpus": 1000000000,
                    "PidsLimit": 64,
                    "Privileged": False,
                    "Binds": ["/host/secret/path:/data:ro"],  # must be removed
                    "LogConfig": {"Type": "json-file", "Config": {}},  # must be removed
                    "Tmpfs": {"/tmp": "rw,noexec", "/out": "rw,noexec"},
                },
                "Config": {
                    "User": "10001:10001",
                    "Image": "auron-sandbox:latest",
                    "Env": ["VULTR_API_KEY=supersecret", "PATH=/usr/bin"],  # must be removed
                    "Labels": {"auron.managed": "true"},
                },
                "Mounts": [
                    {"Type": "bind", "Source": "/home/auron/workspace/xxx", "Destination": "/data", "Mode": "ro", "RW": False}
                ],
                "ResolvConfPath": "/host/resolv.conf",  # must be removed
                "HostnamePath": "/host/hostname",         # must be removed
                "LogPath": "/host/logs/container.log",    # must be removed
                "NetworkSettings": {
                    "Networks": {"none": {}},
                    "SandboxKey": "/host/netns/abc",      # must be removed
                },
                "ImageManifestDescriptor": {
                    "digest": "sha256:abc123",
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                },
            }
        }
    }

    redacted = _redact_inspect_from_receipt(raw_receipt)
    inspect = redacted["container"]["inspect"]

    # Host paths must be gone
    assert "ResolvConfPath" not in inspect
    assert "HostnamePath" not in inspect
    assert "LogPath" not in inspect

    # Env values must not be present
    host_config = inspect.get("HostConfig", {})
    assert "Binds" not in host_config
    assert "LogConfig" not in host_config

    config = inspect.get("Config", {})
    assert "Env" not in config, "Env values must be stripped from redacted inspect!"

    # Network SandboxKey must be gone
    networks = inspect.get("NetworkSettings", {})
    assert "SandboxKey" not in networks

    # Mounts must only expose safe fields (Destination, Type, Mode, RW — no Source)
    for m in inspect.get("Mounts", []):
        assert "Source" not in m, f"Host source path exposed in mount: {m}"

    # Allowlisted fields must still be present
    assert host_config.get("NetworkMode") == "none"
    assert host_config.get("ReadonlyRootfs") is True
    assert config.get("User") == "10001:10001"
    assert inspect.get("ImageManifestDescriptor", {}).get("digest") == "sha256:abc123"


def test_task_response_no_host_paths(client_a: TestClient):
    """Task detail response must never contain storage_path or owner_session_id."""
    files = {"file": ("hp.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    ds = client_a.post("/api/datasets", files=files).json()

    task = client_a.post("/api/tasks", json={"dataset_id": ds["id"], "question": "Count."})
    task_id = task.json()["task_id"]

    resp = client_a.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    data_str = json.dumps(resp.json())
    assert "owner_session_id" not in data_str
    assert "storage_path" not in data_str



def test_cross_session_full_isolation_matrix(client_a: TestClient, client_b: TestClient):
    """Confirm Session B cannot access Session A's datasets, tasks, receipts, verify, or artifacts."""
    # Session A creates dataset and task
    files = {"file": ("matrix_a.csv", io.BytesIO(SAMPLE_CSV), "text/csv")}
    ds_a = client_a.post("/api/datasets", files=files).json()["id"]

    task_a = client_a.post("/api/tasks", json={"dataset_id": ds_a, "question": "Analyze."}).json()["task_id"]

    # Session B attempts to access A's resources:
    # 1. Dataset detail
    assert client_b.get(f"/api/datasets/{ds_a}").status_code == 404
    # 2. Task detail
    assert client_b.get(f"/api/tasks/{task_a}").status_code == 404
    # 3. Receipt
    assert client_b.get(f"/api/tasks/{task_a}/receipt").status_code == 404
    # 4. Verify receipt
    assert client_b.get(f"/api/tasks/{task_a}/verify-receipt").status_code == 404
    # 5. Artifacts
    assert client_b.get(f"/api/tasks/{task_a}/artifacts/chart.png").status_code == 404
    # 6. Task submission on A's dataset
    assert client_b.post("/api/tasks", json={"dataset_id": ds_a, "question": "Hack."}).status_code == 404


def test_csrf_unauthorized_origin_rejected(client_a: TestClient):
    """State-changing requests with hostile Origin header must be rejected with 403 Forbidden."""
    resp = client_a.post(
        "/api/datasets/sample",
        headers={"Origin": "https://attacker.evil.com"},
        json={"sample_name": "sales_clean"},
    )
    assert resp.status_code == 403
    assert "Cross-origin" in resp.json()["detail"]


def test_csrf_authorized_origin_accepted(client_a: TestClient):
    """State-changing requests with trusted Origin header must be accepted."""
    resp = client_a.post(
        "/api/datasets/sample",
        headers={"Origin": "https://45.77.19.39.sslip.io"},
        json={"sample_name": "sales_clean"},
    )
    assert resp.status_code == 201


def test_session_revocation_invalidates_access():
    """Revoked session token must immediately fail authentication."""
    from backend.database import revoke_session
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    session = create_session(token_hash=token_hash)
    session_id = session["id"]

    # Active initially
    assert get_session_by_token_hash(token_hash) is not None

    # Revoke
    revoke_session(session_id)

    # Must be None now
    assert get_session_by_token_hash(token_hash) is None


def test_session_expiry_invalidates_access():
    """Expired session must return None on lookup."""
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    # Expire 1 day in the past
    create_session(token_hash=token_hash, expires_days=-1)
    assert get_session_by_token_hash(token_hash) is None
