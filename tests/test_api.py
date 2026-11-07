"""
Tests for AURON FastAPI Endpoints (PRD §14).
Validates health, dataset upload, sample loading, task creation, and path safety.
"""

import io
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.database import init_db


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path: Path):
    # Point test database and storage to temp directories
    test_db = tmp_path / "api_test.db"
    settings.db_path = test_db
    settings.uploads_dir = tmp_path / "uploads"
    settings.samples_dir = Path("data/samples")
    settings.ensure_directories()
    init_db(test_db)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client: TestClient):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "docker_available" in data
    assert "inference_mode" in data


def test_upload_dataset_endpoint(client: TestClient):
    csv_bytes = b"product,price,units\nLaptop,1200,5\nMouse,25,20\n"
    files = {"file": ("test_sales.csv", io.BytesIO(csv_bytes), "text/csv")}
    resp = client.post("/api/datasets", files=files)

    assert resp.status_code == 201
    data = resp.json()
    assert data["filename"] == "test_sales.csv"
    assert data["row_count"] == 2
    assert data["column_count"] == 3
    assert "id" in data
    assert "storage_path" not in data  # Storage path must be stripped from response!
    assert "preview" in data
    assert len(data["preview"]) == 2


def test_list_and_get_dataset(client: TestClient):
    # Upload first
    csv_bytes = b"a,b\n1,2\n"
    client.post("/api/datasets", files={"file": ("a.csv", io.BytesIO(csv_bytes), "text/csv")})

    resp = client.get("/api/datasets")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    ds_id = items[0]["id"]

    # Single dataset
    single_resp = client.get(f"/api/datasets/{ds_id}")
    assert single_resp.status_code == 200
    assert single_resp.json()["id"] == ds_id


def test_load_sample_dataset(client: TestClient):
    resp = client.post("/api/datasets/sample", json={"sample_name": "sales_clean"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_sample"] is True
    assert data["filename"] == "sales_clean.csv"
    assert data["row_count"] > 0


def test_create_task_endpoint(client: TestClient):
    # 1. Load sample dataset
    sample_resp = client.post("/api/datasets/sample", json={"sample_name": "sales_clean"})
    ds_id = sample_resp.json()["id"]

    # 2. Create task
    task_resp = client.post("/api/tasks", json={
        "dataset_id": ds_id,
        "question": "What is the average revenue per product?"
    })
    assert task_resp.status_code == 202
    task_data = task_resp.json()
    assert "task_id" in task_data
    assert task_data["status"] == "pending"

    # 3. Poll task
    get_resp = client.get(f"/api/tasks/{task_data['task_id']}")
    assert get_resp.status_code == 200
    t = get_resp.json()
    assert t["id"] == task_data["task_id"]
    assert t["question"] == "What is the average revenue per product?"


def test_artifact_path_traversal_blocked(client: TestClient):
    # Attempting to fetch an artifact with ../ path traversal must return 400 or 404
    resp = client.get("/api/tasks/fake-id/artifacts/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)

