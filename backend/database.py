"""
AURON SQLite Database Layer
Handles schema creation, connections with WAL mode, persistent CRUD operations,
anonymous workspace sessions, and strict ownership isolation.
Source of Truth: PRD v3.0 §13 & §15.
"""

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from backend.config import settings

logger = logging.getLogger("auron.database")

LEGACY_SESSION_ID = "00000000-0000-0000-0000-legacy000000"


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
    """Initialize database tables and run safe incremental migrations."""
    conn = get_db_connection(db_path)
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            token_hash TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        );

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
            owner_session_id TEXT,
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
            owner_session_id TEXT,
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
            owner_session_id TEXT,
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
            owner_session_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id),
            FOREIGN KEY (attempt_id) REFERENCES attempts(id)
        );

        CREATE TABLE IF NOT EXISTS inference_calls (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            role TEXT NOT NULL,
            model_name TEXT NOT NULL,
            requested_model TEXT,
            returned_model TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            duration_ms REAL DEFAULT 0.0,
            success INTEGER DEFAULT 1,
            error_message TEXT,
            owner_session_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );

        CREATE TABLE IF NOT EXISTS redteam_runs (
            id TEXT PRIMARY KEY,
            probe_id TEXT NOT NULL,
            probe_name TEXT NOT NULL,
            expected TEXT NOT NULL,
            observed TEXT NOT NULL,
            mechanism TEXT NOT NULL,
            classification TEXT NOT NULL,
            started_at TEXT NOT NULL,
            duration_ms REAL DEFAULT 0.0,
            evidence_json TEXT NOT NULL,
            owner_session_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
        CREATE INDEX IF NOT EXISTS idx_tasks_dataset ON tasks(dataset_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(task_id);
        CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
        CREATE INDEX IF NOT EXISTS idx_inference_task ON inference_calls(task_id);
        CREATE INDEX IF NOT EXISTS idx_redteam_probe ON redteam_runs(probe_id);
        """)

        # Incremental migrations for existing database instances: ensure owner_session_id columns exist
        for table in ["datasets", "tasks", "attempts", "artifacts", "inference_calls", "redteam_runs"]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_session_id TEXT")
            except Exception:
                pass

        try:
            conn.execute("ALTER TABLE inference_calls ADD COLUMN requested_model TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE inference_calls ADD COLUMN returned_model TEXT")
        except Exception:
            pass

        # Create reserved legacy session record
        legacy_hash = hashlib.sha256(b"auron_reserved_legacy_session").hexdigest()
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions (id, token_hash, created_at, expires_at, revoked_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (LEGACY_SESSION_ID, legacy_hash, "2026-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00")
        )

        # Backfill existing legacy records that do not have an owner_session_id
        for table in ["datasets", "tasks", "attempts", "artifacts", "inference_calls", "redteam_runs"]:
            conn.execute(
                f"UPDATE {table} SET owner_session_id = ? WHERE owner_session_id IS NULL",
                (LEGACY_SESSION_ID,)
            )

        # Create ownership indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_datasets_owner ON datasets(owner_session_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_session_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_redteam_owner ON redteam_runs(owner_session_id);")

        # Create unique constraint on (owner_session_id, filename) for sample datasets to guarantee idempotency
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_datasets_session_sample ON datasets(owner_session_id, filename) WHERE is_sample = 1;")
        except Exception:
            pass

    conn.close()


# ─── Sessions CRUD ────────────────────────────────────────────────────────────


def create_session(
    token_hash: str,
    expires_days: int = 30,
    session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Create a new server-side session record."""
    s_id = session_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=expires_days)
    now_iso = now.isoformat()
    expires_iso = expires.isoformat()

    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO sessions (id, token_hash, created_at, expires_at, revoked_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (s_id, token_hash, now_iso, expires_iso)
        )
    conn.close()
    return {
        "id": s_id,
        "token_hash": token_hash,
        "created_at": now_iso,
        "expires_at": expires_iso,
        "revoked_at": None,
    }


def get_session_by_token_hash(token_hash: str, db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Look up an active, unrevoked, non-expired session by token hash."""
    conn = get_db_connection(db_path)
    cur = conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None

    s = dict(row)
    if s.get("revoked_at"):
        return None

    # Check expiration
    try:
        exp_dt = datetime.fromisoformat(s["expires_at"])
        if datetime.now(timezone.utc) >= exp_dt:
            return None
    except Exception:
        pass

    return s


def get_session(session_id: str, db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Retrieve session by UUID."""
    conn = get_db_connection(db_path)
    cur = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def revoke_session(session_id: str, db_path: Optional[Path] = None) -> None:
    """Revoke a session immediately."""
    now = _utc_now_iso()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE id = ?", (now, session_id))
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
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Store a newly validated dataset record with owner session."""
    d_id = dataset_id or str(uuid.uuid4())
    owner_id = owner_session_id or LEGACY_SESSION_ID
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
                is_sample, owner_session_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                d_id, filename, storage_path, file_hash, file_size_bytes,
                row_count, column_count, encoding, schema_str, preview_str,
                1 if is_sample else 0, owner_id, now
            )
        )
    conn.close()
    return get_dataset(d_id, owner_session_id=None, db_path=db_path)  # type: ignore


def get_dataset(
    dataset_id: str,
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """
    Retrieve a dataset by UUID.
    If owner_session_id is provided, enforces that dataset is owned by that session.
    """
    conn = get_db_connection(db_path)
    if owner_session_id is not None:
        cur = conn.execute("SELECT * FROM datasets WHERE id = ? AND owner_session_id = ?", (dataset_id, owner_session_id))
    else:
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


def get_dataset_by_name_and_session(
    filename: str,
    owner_session_id: str,
    is_sample: bool = True,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Retrieve an existing sample dataset for a session to prevent duplicates."""
    conn = get_db_connection(db_path)
    cur = conn.execute(
        "SELECT * FROM datasets WHERE filename = ? AND owner_session_id = ? AND is_sample = ? ORDER BY created_at DESC LIMIT 1",
        (filename, owner_session_id, 1 if is_sample else 0),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    res = dict(row)
    res["is_sample"] = bool(res["is_sample"])
    res["schema"] = json.loads(res["schema_json"])
    res["preview"] = json.loads(res["preview_json"])
    return res


def list_datasets(
    owner_session_id: Optional[str] = None,
    include_samples: bool = True,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """
    List datasets. If owner_session_id is provided, strictly restricts to that session.
    """
    conn = get_db_connection(db_path)
    if owner_session_id is not None:
        if include_samples:
            cur = conn.execute("SELECT * FROM datasets WHERE owner_session_id = ? ORDER BY created_at DESC", (owner_session_id,))
        else:
            cur = conn.execute("SELECT * FROM datasets WHERE owner_session_id = ? AND is_sample = 0 ORDER BY created_at DESC", (owner_session_id,))
    else:
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
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Create a new task with 'pending' status and session ownership."""
    t_id = task_id or str(uuid.uuid4())
    owner_id = owner_session_id or LEGACY_SESSION_ID
    now = _utc_now_iso()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, dataset_id, question, status, owner_session_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (t_id, dataset_id, question, "pending", owner_id, now)
        )
    conn.close()
    return get_task(t_id, owner_session_id=None, db_path=db_path)  # type: ignore


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


def get_task(
    task_id: str,
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Retrieve task details, attempts, artifacts, and inference records."""
    conn = get_db_connection(db_path)
    if owner_session_id is not None:
        cur = conn.execute("SELECT * FROM tasks WHERE id = ? AND owner_session_id = ?", (task_id, owner_session_id))
    else:
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


def list_tasks(
    owner_session_id: Optional[str] = None,
    dataset_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """List recent tasks, restricted to owner session when provided."""
    conn = get_db_connection(db_path)
    clauses = []
    params: list[Any] = []
    if owner_session_id is not None:
        clauses.append("owner_session_id = ?")
        params.append(owner_session_id)
    if dataset_id:
        clauses.append("dataset_id = ?")
        params.append(dataset_id)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM tasks {where_sql} ORDER BY created_at DESC"
    cur = conn.execute(sql, params)
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
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Record a container execution attempt."""
    att_id = attempt_id or str(uuid.uuid4())
    owner_id = owner_session_id or LEGACY_SESSION_ID
    now = _utc_now_iso()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO attempts (
                id, task_id, attempt_number, code, exit_code, status,
                stdout, stderr, stdout_truncated, stderr_truncated,
                duration_ms, timed_out, oom_killed, receipt_json, owner_session_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                att_id, task_id, attempt_number, code, exit_code, status,
                stdout, stderr, 1 if stdout_truncated else 0, 1 if stderr_truncated else 0,
                duration_ms, 1 if timed_out else 0, 1 if oom_killed else 0,
                receipt_json, owner_id, now
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
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Record an output artifact."""
    art_id = str(uuid.uuid4())
    owner_id = owner_session_id or LEGACY_SESSION_ID
    now = _utc_now_iso()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO artifacts (
                id, task_id, attempt_id, filename, storage_path,
                file_size, sha256, mime, owner_session_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (art_id, task_id, attempt_id, filename, storage_path, file_size, sha256, mime, owner_id, now)
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
    requested_model: Optional[str] = None,
    returned_model: Optional[str] = None,
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Record an LLM inference call for observability and audit."""
    call_id = str(uuid.uuid4())
    owner_id = owner_session_id or LEGACY_SESSION_ID
    now = _utc_now_iso()
    req_m = requested_model or model_name
    ret_m = returned_model or model_name
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO inference_calls (
                id, task_id, role, model_name, requested_model, returned_model,
                prompt_tokens, completion_tokens, duration_ms, success, error_message, owner_session_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id, task_id, role, ret_m, req_m, ret_m,
                prompt_tokens, completion_tokens, duration_ms, 1 if success else 0, error_message, owner_id, now
            )
        )
    conn.close()
    return call_id


# ─── Red Team Runs CRUD ───────────────────────────────────────────────────────


def record_redteam_run(
    probe_id: str,
    probe_name: str,
    expected: str,
    observed: str,
    mechanism: str,
    classification: str,
    started_at: str,
    duration_ms: float = 0.0,
    evidence: Optional[dict[str, Any]] = None,
    run_id: Optional[str] = None,
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Record a Red Team probe execution result."""
    rid = run_id or str(uuid.uuid4())
    owner_id = owner_session_id or LEGACY_SESSION_ID
    evidence_json = json.dumps(evidence or {})
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO redteam_runs (
                id, probe_id, probe_name, expected, observed, mechanism,
                classification, started_at, duration_ms, evidence_json, owner_session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid, probe_id, probe_name, expected, observed, mechanism,
                classification, started_at, duration_ms, evidence_json, owner_id
            )
        )
    conn.close()
    return rid


def list_redteam_runs(
    limit: int = 50,
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """List recent Red Team probe runs ordered by started_at DESC."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    if owner_session_id is not None:
        cursor.execute(
            """
            SELECT id, probe_id, probe_name, expected, observed, mechanism,
                   classification, started_at, duration_ms, evidence_json
            FROM redteam_runs
            WHERE owner_session_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (owner_session_id, limit)
        )
    else:
        cursor.execute(
            """
            SELECT id, probe_id, probe_name, expected, observed, mechanism,
                   classification, started_at, duration_ms, evidence_json
            FROM redteam_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,)
        )
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["evidence"] = json.loads(d["evidence_json"])
        except Exception:
            d["evidence"] = {}
        result.append(d)
    return result


def get_redteam_run(
    run_id: str,
    owner_session_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Get a single Red Team run by ID."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    if owner_session_id is not None:
        cursor.execute(
            """
            SELECT id, probe_id, probe_name, expected, observed, mechanism,
                   classification, started_at, duration_ms, evidence_json
            FROM redteam_runs
            WHERE id = ? AND owner_session_id = ?
            """,
            (run_id, owner_session_id)
        )
    else:
        cursor.execute(
            """
            SELECT id, probe_id, probe_name, expected, observed, mechanism,
                   classification, started_at, duration_ms, evidence_json
            FROM redteam_runs
            WHERE id = ?
            """,
            (run_id,)
        )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["evidence"] = json.loads(d["evidence_json"])
    except Exception:
        d["evidence"] = {}
    return d
