"""
AURON SQLite Database Layer
Handles schema creation, connections with WAL mode, and persistent CRUD operations.
Source of Truth: PRD v3.0 §13 & §15.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import settings


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open a connection to SQLite database with WAL mode and foreign keys enabled.
    Uses sqlite3.Row for dictionary-like column access.
    """
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize database tables per PRD §13 and §15."""
    conn = get_db_connection(db_path)
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS datasets (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            encoding TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            preview_json TEXT NOT NULL,
            is_sample INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            question TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_json TEXT,
            final_code TEXT,
            summary_md TEXT,
            error_message TEXT,
            receipt_hash TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (dataset_id) REFERENCES datasets(id)
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            code TEXT NOT NULL,
            exit_code INTEGER,
            status TEXT NOT NULL,
            stdout TEXT,
            stderr TEXT,
            stdout_truncated INTEGER DEFAULT 0,
            stderr_truncated INTEGER DEFAULT 0,
            duration_ms REAL,
            timed_out INTEGER DEFAULT 0,
            oom_killed INTEGER DEFAULT 0,
            receipt_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            attempt_id TEXT,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            mime TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id),
            FOREIGN KEY (attempt_id) REFERENCES attempts(id)
        );

        CREATE TABLE IF NOT EXISTS inference_calls (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            role TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            duration_ms REAL DEFAULT 0.0,
            success INTEGER DEFAULT 1,
            error_message TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_dataset ON tasks(dataset_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(task_id);
        CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
        CREATE INDEX IF NOT EXISTS idx_inference_task ON inference_calls(task_id);
        """)
    conn.close()


# ─── Datasets CRUD ────────────────────────────────────────────────────────────


def create_dataset(
    filename: str,
    storage_path: str,
    file_hash: str,
    file_size_bytes: int,
    row_count: int,
    column_count: int,
    encoding: str,
    schema: dict,
    preview: list[dict],
    is_sample: bool = False,
    dataset_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Store a newly validated dataset record."""
    d_id = dataset_id or str(uuid.uuid4())
    now = _utc_now_iso()
    schema_str = json.dumps(schema)
    preview_str = json.dumps(preview)

    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO datasets (
                id, filename, storage_path, file_hash, file_size_bytes,
                row_count, column_count, encoding, schema_json, preview_json,
                is_sample, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                d_id, filename, storage_path, file_hash, file_size_bytes,
                row_count, column_count, encoding, schema_str, preview_str,
                1 if is_sample else 0, now
            )
        )
    conn.close()
    return get_dataset(d_id, db_path=db_path)  # type: ignore


def get_dataset(dataset_id: str, db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Retrieve a dataset by its UUID."""
    conn = get_db_connection(db_path)
    cur = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    res = dict(row)
    res["is_sample"] = bool(res["is_sample"])
    res["schema"] = json.loads(res["schema_json"])
    res["preview"] = json.loads(res["preview_json"])
    return res


def list_datasets(include_samples: bool = True, db_path: Optional[Path] = None) -> list[dict[str, Any]]:
    """List datasets, optionally filtering out samples."""
    conn = get_db_connection(db_path)
    if include_samples:
        cur = conn.execute("SELECT * FROM datasets ORDER BY created_at DESC")
    else:
        cur = conn.execute("SELECT * FROM datasets WHERE is_sample = 0 ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["is_sample"] = bool(d["is_sample"])
        d["schema"] = json.loads(d["schema_json"])
        d["preview"] = json.loads(d["preview_json"])
        out.append(d)
    return out


# ─── Tasks CRUD ───────────────────────────────────────────────────────────────


def create_task(
    dataset_id: str,
    question: str,
    task_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Create a new task with 'pending' status."""
    t_id = task_id or str(uuid.uuid4())
    now = _utc_now_iso()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, dataset_id, question, status, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (t_id, dataset_id, question, "pending", now)
        )
    conn.close()
    return get_task(t_id, db_path=db_path)  # type: ignore


def update_task_status(
    task_id: str,
    status: str,
    plan_json: Optional[str] = None,
    final_code: Optional[str] = None,
    summary_md: Optional[str] = None,
    error_message: Optional[str] = None,
    receipt_hash: Optional[str] = None,
    completed_at: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    """Update task status and optional execution metadata."""
    conn = get_db_connection(db_path)
    updates = ["status = ?"]
    params: list[Any] = [status]

    if plan_json is not None:
        updates.append("plan_json = ?")
        params.append(plan_json)
    if final_code is not None:
        updates.append("final_code = ?")
        params.append(final_code)
    if summary_md is not None:
        updates.append("summary_md = ?")
        params.append(summary_md)
    if error_message is not None:
        updates.append("error_message = ?")
        params.append(error_message)
    if receipt_hash is not None:
        updates.append("receipt_hash = ?")
        params.append(receipt_hash)
    if completed_at is not None:
        updates.append("completed_at = ?")
        params.append(completed_at)

    params.append(task_id)
    sql = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"
    with conn:
        conn.execute(sql, params)
    conn.close()


def get_task(task_id: str, db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Retrieve task details, attempts, artifacts, and inference records."""
    conn = get_db_connection(db_path)
    cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    task_dict = dict(row)
    if task_dict.get("plan_json"):
        try:
            task_dict["plan"] = json.loads(task_dict["plan_json"])
        except Exception:
            task_dict["plan"] = None
    else:
        task_dict["plan"] = None

    # Fetch attempts
    cur = conn.execute(
        "SELECT * FROM attempts WHERE task_id = ? ORDER BY attempt_number ASC",
        (task_id,)
    )
    task_dict["attempts"] = [dict(r) for r in cur.fetchall()]

    # Fetch artifacts
    cur = conn.execute(
        "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at ASC",
        (task_id,)
    )
    task_dict["artifacts"] = [dict(r) for r in cur.fetchall()]

    # Fetch inference calls
    cur = conn.execute(
        "SELECT * FROM inference_calls WHERE task_id = ? ORDER BY created_at ASC",
        (task_id,)
    )
    task_dict["inference_calls"] = [dict(r) for r in cur.fetchall()]

    conn.close()
    return task_dict


def list_tasks(dataset_id: Optional[str] = None, db_path: Optional[Path] = None) -> list[dict[str, Any]]:
    """List recent tasks, optionally filtered by dataset."""
    conn = get_db_connection(db_path)
    if dataset_id:
        cur = conn.execute(
            "SELECT * FROM tasks WHERE dataset_id = ? ORDER BY created_at DESC",
            (dataset_id,)
        )
    else:
        cur = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Attempts & Artifacts ────────────────────────────────────────────────────


def record_attempt(
    task_id: str,
    attempt_number: int,
    code: str,
    exit_code: Optional[int],
    status: str,
    stdout: str,
    stderr: str,
    stdout_truncated: bool,
    stderr_truncated: bool,
    duration_ms: float,
    timed_out: bool,
    oom_killed: bool,
    receipt_json: str,
    attempt_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Record a container execution attempt."""
    att_id = attempt_id or str(uuid.uuid4())
    now = _utc_now_iso()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO attempts (
                id, task_id, attempt_number, code, exit_code, status,
                stdout, stderr, stdout_truncated, stderr_truncated,
                duration_ms, timed_out, oom_killed, receipt_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                att_id, task_id, attempt_number, code, exit_code, status,
                stdout, stderr, 1 if stdout_truncated else 0, 1 if stderr_truncated else 0,
                duration_ms, 1 if timed_out else 0, 1 if oom_killed else 0,
                receipt_json, now
            )
        )
    conn.close()
    return att_id


def record_artifact(
    task_id: str,
    filename: str,
    storage_path: str,
    file_size: int,
    sha256: str,
    mime: str,
    attempt_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Record an output artifact."""
    art_id = str(uuid.uuid4())
    now = _utc_now_iso()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO artifacts (
                id, task_id, attempt_id, filename, storage_path,
                file_size, sha256, mime, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (art_id, task_id, attempt_id, filename, storage_path, file_size, sha256, mime, now)
        )
    conn.close()
    return art_id


def record_inference_call(
    task_id: str,
    role: str,
    model_name: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: float = 0.0,
    success: bool = True,
    error_message: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Record an LLM inference call for observability and audit."""
    call_id = str(uuid.uuid4())
    now = _utc_now_iso()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO inference_calls (
                id, task_id, role, model_name, prompt_tokens,
                completion_tokens, duration_ms, success, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id, task_id, role, model_name, prompt_tokens,
                completion_tokens, duration_ms, 1 if success else 0, error_message, now
            )
        )
    conn.close()
    return call_id

