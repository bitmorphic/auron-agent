"""
Tests for SQLite Database Persistence Layer (PRD §13 & §15).
Validates schema initialization, UUID usage, task state transitions, and audit records.
"""

import json
from pathlib import Path
import pytest
from backend.database import (
    create_dataset,
    create_task,
    get_dataset,
    get_task,
    init_db,
    list_datasets,
    list_tasks,
    record_artifact,
    record_attempt,
    record_inference_call,
    update_task_status,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_auron.db"
    init_db(db_file)
    return db_file


def test_dataset_lifecycle(temp_db: Path):
    ds = create_dataset(
        filename="test.csv",
        storage_path="/data/test.csv",
        file_hash="abc123hash",
        file_size_bytes=1024,
        row_count=50,
        column_count=4,
        encoding="utf-8",
        schema={"columns": [{"name": "col1", "dtype": "integer"}]},
        preview=[{"col1": 1}],
        is_sample=False,
        db_path=temp_db,
    )

    assert ds is not None
    assert ds["filename"] == "test.csv"
    assert ds["row_count"] == 50
    assert ds["is_sample"] is False

    retrieved = get_dataset(ds["id"], db_path=temp_db)
    assert retrieved is not None
    assert retrieved["id"] == ds["id"]
    assert retrieved["schema"]["columns"][0]["name"] == "col1"

    all_ds = list_datasets(db_path=temp_db)
    assert len(all_ds) == 1


def test_task_lifecycle_and_transitions(temp_db: Path):
    ds = create_dataset(
        filename="sales.csv",
        storage_path="/data/sales.csv",
        file_hash="hash123",
        file_size_bytes=2048,
        row_count=100,
        column_count=3,
        encoding="utf-8",
        schema={"columns": []},
        preview=[],
        db_path=temp_db,
    )

    # 1. Create task
    task = create_task(dataset_id=ds["id"], question="Total sales?", db_path=temp_db)
    assert task["status"] == "pending"
    assert task["dataset_id"] == ds["id"]

    # 2. Transition: planning
    update_task_status(task["id"], "planning", db_path=temp_db)
    t = get_task(task["id"], db_path=temp_db)
    assert t["status"] == "planning"

    # 3. Transition: generating with plan
    plan_data = {"goal": "Sum sales", "steps": ["load", "sum"]}
    update_task_status(
        task["id"],
        "generating",
        plan_json=json.dumps(plan_data),
        db_path=temp_db,
    )
    t = get_task(task["id"], db_path=temp_db)
    assert t["status"] == "generating"
    assert t["plan"]["goal"] == "Sum sales"

    # 4. Transition: running with code
    update_task_status(task["id"], "running", final_code="print('hello')", db_path=temp_db)
    t = get_task(task["id"], db_path=temp_db)
    assert t["status"] == "running"
    assert t["final_code"] == "print('hello')"

    # 5. Record execution attempt
    att_id = record_attempt(
        task_id=task["id"],
        attempt_number=1,
        code="print('hello')",
        exit_code=0,
        status="completed",
        stdout="hello\n",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        duration_ms=450.0,
        timed_out=False,
        oom_killed=False,
        receipt_json="{}",
        db_path=temp_db,
    )
    assert att_id is not None

    # 6. Record artifact
    art_id = record_artifact(
        task_id=task["id"],
        attempt_id=att_id,
        filename="chart.png",
        storage_path="/storage/chart.png",
        file_size=12345,
        sha256="arthash",
        mime="image/png",
        db_path=temp_db,
    )
    assert art_id is not None

    # 7. Record inference call
    call_id = record_inference_call(
        task_id=task["id"],
        role="planner",
        model_name="mock-llama",
        prompt_tokens=50,
        completion_tokens=100,
        duration_ms=150.0,
        success=True,
        db_path=temp_db,
    )
    assert call_id is not None

    # 8. Complete task
    update_task_status(
        task["id"],
        "completed",
        summary_md="Sales analysis complete.",
        receipt_hash="receipthash999",
        completed_at="2026-11-06T12:00:00Z",
        db_path=temp_db,
    )

    final_task = get_task(task["id"], db_path=temp_db)
    assert final_task["status"] == "completed"
    assert final_task["summary_md"] == "Sales analysis complete."
    assert final_task["receipt_hash"] == "receipthash999"
    assert len(final_task["attempts"]) == 1
    assert len(final_task["artifacts"]) == 1
    assert len(final_task["inference_calls"]) == 1

