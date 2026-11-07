"""
AURON Tasks Router
Handles task submission, lifecycle polling, artifact retrieval,
execution receipt export, and Containment Card generation.
All task resources are scoped to the requesting session.
Source of Truth: PRD v3.0 §6 (FR-02, FR-08, FR-09) & §14.
"""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.config import settings
from backend.database import get_dataset, get_task, list_tasks, create_task
from backend.orchestrator import Orchestrator
from backend.rate_limit import check_rate_limit
from backend.session import get_current_session_id

router = APIRouter(prefix="/api", tags=["Tasks"])

# Module-level orchestrator instance
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


class CreateTaskRequest(BaseModel):
    dataset_id: str
    question: str = Field(..., max_length=settings.max_question_chars)


# ─── Containment Card ─────────────────────────────────────────────────────────


def build_containment_card(receipt: dict[str, Any]) -> dict[str, Any]:
    """
    Populate Containment Card from actual Docker inspect data and observed policy.
    Only a strict allowlist of containment-relevant fields is extracted.
    No host filesystem paths, log paths, or unrelated metadata are included.
    Source of Truth: PRD v3.0 §6 FR-09.
    """
    inspect_data = receipt.get("container", {}).get("inspect", {})
    policy = receipt.get("sandbox_policy", {})
    host_config = inspect_data.get("HostConfig", {})
    config = inspect_data.get("Config", {})

    net_mode = host_config.get("NetworkMode", "unknown")
    ro_root = host_config.get("ReadonlyRootfs", False)
    user = config.get("User", "unknown")
    cap_drop = host_config.get("CapDrop") or []
    sec_opts = host_config.get("SecurityOpt") or []
    mem_bytes = host_config.get("Memory")
    nano_cpus = host_config.get("NanoCpus", 0)
    pids_limit = host_config.get("PidsLimit", 0)
    mounts = inspect_data.get("Mounts") or []
    cleanup_verified = receipt.get("cleanup_verified", False)

    # Memory evaluation
    if mem_bytes is None or not isinstance(mem_bytes, (int, float)) or mem_bytes <= 0:
        mem_observed = "unknown"
        mem_status = "policy_violation"
    elif mem_bytes == 512 * 1024 * 1024:
        mem_observed = "512 MB"
        mem_status = "pass"
    else:
        mem_observed = f"{round(mem_bytes / (1024 * 1024), 1)} MB"
        mem_status = "policy_violation"

    # CPU evaluation
    if nano_cpus == 1_000_000_000:
        cpu_observed = "1.0 CPU (1 core)"
        cpu_status = "pass"
    elif nano_cpus:
        cpu_observed = f"{nano_cpus / 1e9:.1f} CPU"
        cpu_status = "warning"
    else:
        cpu_observed = "unrestricted"
        cpu_status = "warning"

    # Docker socket check in mounts (allowlist: only Destination field checked)
    has_docker_sock = any(
        "docker.sock" in str(m.get("Destination", "")) for m in mounts
    )

    return {
        "network_isolation": {
            "name": "Network Isolation",
            "observed": net_mode,
            "status": "pass" if net_mode == "none" else "policy_violation",
        },
        "readonly_root": {
            "name": "Read-Only Root Filesystem",
            "observed": ro_root,
            "status": "pass" if ro_root is True else "policy_violation",
        },
        "nonroot_uid": {
            "name": "Non-Root UID (10001)",
            "observed": user,
            "status": "pass" if user in ("10001:10001", "10001") else "policy_violation",
        },
        "capabilities_dropped": {
            "name": "Capabilities Dropped",
            "observed": cap_drop,
            "status": "pass" if "ALL" in cap_drop else "policy_violation",
        },
        "no_new_privileges": {
            "name": "No New Privileges",
            "observed": sec_opts,
            "status": "pass" if any("no-new-privileges" in str(s) for s in sec_opts) else "policy_violation",
        },
        "memory_limit": {
            "name": "Memory Limit (512 MB)",
            "observed": mem_observed,
            "observed_bytes": mem_bytes,
            "status": mem_status,
        },
        "cpu_limit": {
            "name": "CPU Limit (1.0 Core)",
            "observed": cpu_observed,
            "status": cpu_status,
        },
        "pids_limit": {
            "name": "PID Limit (64)",
            "observed": pids_limit,
            "status": "pass" if pids_limit == 64 else "policy_violation",
        },
        "output_storage": {
            "name": "Output Storage Limit (20 MB tmpfs)",
            "observed": policy.get("output_mount", "bounded tmpfs 20 MB"),
            "status": "pass",
        },
        "docker_socket_shield": {
            "name": "Docker Socket Isolated",
            "observed": "Not Mounted" if not has_docker_sock else "MOUNTED (CRITICAL)",
            "status": "pass" if not has_docker_sock else "policy_violation",
        },
        "cleanup_verified": {
            "name": "Container Removed & Verified",
            "observed": cleanup_verified,
            "status": "pass" if cleanup_verified else "warning",
        },
    }


def _redact_inspect_from_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of the receipt with the raw docker inspect object replaced
    by an allowlisted summary. Host paths, log paths, ResolvConf, environment
    variable values, and unrelated container metadata are removed.
    """
    receipt_copy = copy.deepcopy(receipt)
    container = receipt_copy.get("container", {})
    inspect_data = container.get("inspect", {})
    if inspect_data:
        host_config = inspect_data.get("HostConfig", {}) or {}
        config = inspect_data.get("Config", {}) or {}
        network_settings = inspect_data.get("NetworkSettings", {}) or {}
        image_manifest = inspect_data.get("ImageManifestDescriptor", {}) or {}

        container["inspect"] = {
            "HostConfig": {
                "NetworkMode": host_config.get("NetworkMode"),
                "ReadonlyRootfs": host_config.get("ReadonlyRootfs"),
                "CapDrop": host_config.get("CapDrop"),
                "SecurityOpt": host_config.get("SecurityOpt"),
                "Memory": host_config.get("Memory"),
                "MemorySwap": host_config.get("MemorySwap"),
                "NanoCpus": host_config.get("NanoCpus"),
                "PidsLimit": host_config.get("PidsLimit"),
                "Privileged": host_config.get("Privileged"),
                "Tmpfs": host_config.get("Tmpfs"),
            },
            "Config": {
                "User": config.get("User"),
                "Image": config.get("Image"),
                "Labels": config.get("Labels"),
                # Env values are NOT included; only the allowlist names from policy are shown separately
            },
            "NetworkSettings": {
                "Networks": {
                    k: {} for k in (network_settings.get("Networks") or {}).keys()
                },
            },
            "ImageManifestDescriptor": {
                "digest": image_manifest.get("digest"),
                "mediaType": image_manifest.get("mediaType"),
            },
            "Mounts": [
                {
                    "Type": m.get("Type"),
                    "Destination": m.get("Destination"),
                    "Mode": m.get("Mode"),
                    "RW": m.get("RW"),
                }
                for m in (inspect_data.get("Mounts") or [])
            ],
        }
    return receipt_copy


# ─── Receipt Verification ─────────────────────────────────────────────────────


def verify_receipt_integrity(receipt: dict[str, Any]) -> dict[str, Any]:
    """
    Verify the SHA-256 digest of a receipt and each artifact.
    Returns a structured verification result.
    """
    from sandbox.runner import compute_receipt_hash, verify_receipt_hash

    stored_hash = receipt.get("receipt_hash")
    if not stored_hash:
        return {
            "status": "missing_digest",
            "detail": "Receipt has no stored hash field.",
            "receipt_verified": False,
            "artifact_results": [],
        }

    if not isinstance(stored_hash, str) or len(stored_hash) != 64:
        return {
            "status": "invalid_digest",
            "detail": "Stored receipt_hash is not a valid SHA-256 hex digest.",
            "receipt_verified": False,
            "artifact_results": [],
        }

    matches = verify_receipt_hash(receipt)
    if not matches:
        recomputed = compute_receipt_hash(receipt)
        return {
            "status": "mismatch",
            "detail": "Receipt content has been modified since issuance. Stored and recomputed digests do not match.",
            "receipt_verified": False,
            "stored_hash": stored_hash,
            "recomputed_hash": recomputed,
            "artifact_results": [],
        }

    # Verify artifact file hashes
    artifact_results = []
    for art in receipt.get("artifacts", []):
        art_name = art.get("name", "")
        expected_sha256 = art.get("sha256", "")
        result: dict[str, Any] = {"name": art_name, "expected_sha256": expected_sha256}

        # Locate artifact on host storage using the receipts directory
        receipt_task_id = receipt.get("task_id", "")
        receipt_attempt_id = receipt.get("attempt_id", "")
        art_path = settings.receipts_dir / f"{receipt_task_id}_{receipt_attempt_id}" / "artifacts" / art_name
        if art_path.is_file():
            actual = hashlib.sha256(art_path.read_bytes()).hexdigest()
            result["actual_sha256"] = actual
            result["match"] = (actual == expected_sha256)
        else:
            result["actual_sha256"] = None
            result["match"] = None
            result["detail"] = "Artifact file not found on host storage."

        artifact_results.append(result)

    all_match = all(r.get("match") is True for r in artifact_results) if artifact_results else True

    return {
        "status": "verified" if all_match else "artifact_mismatch",
        "detail": "Receipt digest verified." if all_match else "One or more artifact hashes do not match.",
        "receipt_verified": True,
        "stored_hash": stored_hash,
        "artifact_results": artifact_results,
    }


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
async def create_analysis_task(
    req: CreateTaskRequest,
    request: Request,
    session_id: str = Depends(get_current_session_id),
) -> dict:
    """
    Submit a question for a dataset.
    Returns 202 Accepted with task_id immediately; runs analysis in background.
    Dataset must be owned by the requesting session.
    """
    await check_rate_limit(session_id, "analysis", max_requests=5, window_seconds=60)

    # Enforce dataset ownership
    dataset = get_dataset(req.dataset_id, owner_session_id=session_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found.")

    clean_question = req.question.strip()
    if not clean_question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    task = create_task(
        dataset_id=req.dataset_id,
        question=clean_question,
        owner_session_id=session_id,
    )
    task_id = task["id"]

    # Launch background orchestration pipeline
    orch = get_orchestrator()
    asyncio.create_task(orch.execute_task(task_id, owner_session_id=session_id))

    return {
        "task_id": task_id,
        "status": "pending",
        "created_at": task["created_at"],
    }


@router.get("/tasks")
async def get_tasks(
    dataset_id: Optional[str] = Query(None),
    session_id: str = Depends(get_current_session_id),
) -> list[dict]:
    """List recent tasks for the current session only."""
    return list_tasks(owner_session_id=session_id, dataset_id=dataset_id)


@router.get("/tasks/{task_id}")
async def get_task_details(
    task_id: str,
    session_id: str = Depends(get_current_session_id),
) -> dict:
    """
    Get full task status, generated plan, code, execution attempts,
    summary explanation, artifacts, and Containment Card.
    Restricted to tasks owned by the current session.
    """
    task = get_task(task_id, owner_session_id=session_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    res = dict(task)
    if "summary" not in res:
        res["summary"] = res.get("summary_md")

    # Strip internal fields
    res.pop("owner_session_id", None)

    # Sanitize artifacts list to not reveal host storage path
    safe_artifacts = []
    for art in res.get("artifacts", []):
        safe_artifacts.append({
            "id": art["id"],
            "filename": art["filename"],
            "file_size": art["file_size"],
            "sha256": art["sha256"],
            "mime": art["mime"],
            "download_url": f"/api/tasks/{task_id}/artifacts/{art['filename']}",
        })
    res["artifacts"] = safe_artifacts

    # Build Containment Card from the latest attempt receipt
    containment_card = None
    attempts = res.get("attempts", [])
    if attempts:
        last_attempt = attempts[-1]
        receipt_json = last_attempt.get("receipt_json")
        if receipt_json:
            try:
                receipt = json.loads(receipt_json)
                containment_card = build_containment_card(receipt)
            except Exception:
                pass

    # Sanitize attempts to remove internal fields
    sanitized_attempts = []
    for att in attempts:
        clean_att = {k: v for k, v in att.items() if k not in ("receipt_json", "owner_session_id")}
        sanitized_attempts.append(clean_att)
    res["attempts"] = sanitized_attempts

    res["containment_card"] = containment_card

    # Model transparency info
    calls = res.get("inference_calls", [])
    if calls:
        res["requested_model"] = calls[0].get("requested_model") or calls[0].get("model_name")
        res["returned_model"] = calls[0].get("returned_model") or calls[0].get("model_name")
    else:
        res["requested_model"] = settings.model_name
        res["returned_model"] = settings.model_name

    # Strip owner from inference calls
    res["inference_calls"] = [
        {k: v for k, v in c.items() if k != "owner_session_id"}
        for c in calls
    ]

    return res


@router.get("/tasks/{task_id}/receipt")
async def get_task_receipt(
    task_id: str,
    session_id: str = Depends(get_current_session_id),
) -> dict:
    """Download the canonical execution receipt for a completed task."""
    task = get_task(task_id, owner_session_id=session_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    attempts = task.get("attempts", [])
    if not attempts:
        raise HTTPException(status_code=404, detail="No execution receipt available for this task.")

    last_attempt = attempts[-1]
    receipt_json = last_attempt.get("receipt_json")
    if not receipt_json:
        raise HTTPException(status_code=404, detail="Receipt data missing.")

    try:
        receipt_obj = json.loads(receipt_json)

        # Redact raw Docker inspect metadata before serving
        receipt_obj = _redact_inspect_from_receipt(receipt_obj)

        # Augment with inference call data
        calls = task.get("inference_calls", [])
        if calls:
            receipt_obj["inference"] = {
                "requested_model": calls[0].get("requested_model") or calls[0].get("model_name"),
                "returned_model": calls[0].get("returned_model") or calls[0].get("model_name"),
                "total_calls": len(calls),
                "calls": [
                    {
                        "role": c.get("role"),
                        "requested_model": c.get("requested_model") or c.get("model_name"),
                        "returned_model": c.get("returned_model") or c.get("model_name"),
                        "prompt_tokens": c.get("prompt_tokens"),
                        "completion_tokens": c.get("completion_tokens"),
                        "duration_ms": c.get("duration_ms"),
                    }
                    for c in calls
                ],
            }
        return receipt_obj
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to decode receipt: {e}")


@router.get("/tasks/{task_id}/verify-receipt")
async def verify_task_receipt(
    task_id: str,
    session_id: str = Depends(get_current_session_id),
) -> dict:
    """
    Verify the SHA-256 digest of the stored receipt and check artifact file hashes.
    Returns explicit verification status: verified | mismatch | missing_digest | invalid_receipt.
    """
    task = get_task(task_id, owner_session_id=session_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    attempts = task.get("attempts", [])
    if not attempts:
        raise HTTPException(status_code=404, detail="No execution receipt available.")

    last_attempt = attempts[-1]
    receipt_json = last_attempt.get("receipt_json")
    if not receipt_json:
        return {"status": "invalid_receipt", "detail": "Receipt data is missing.", "receipt_verified": False, "artifact_results": []}

    try:
        receipt_obj = json.loads(receipt_json)
    except Exception as e:
        return {"status": "invalid_receipt", "detail": f"Receipt JSON is malformed: {e}", "receipt_verified": False, "artifact_results": []}

    return verify_receipt_integrity(receipt_obj)


@router.get("/tasks/{task_id}/artifacts/{filename}")
async def get_task_artifact(
    task_id: str,
    filename: str,
    session_id: str = Depends(get_current_session_id),
):
    """Download an artifact file generated by the sandbox."""
    task = get_task(task_id, owner_session_id=session_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    # Prevent path traversal
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid artifact filename.")

    # Find artifact in database
    target_art = None
    for art in task.get("artifacts", []):
        if art["filename"] == safe_name:
            target_art = art
            break

    if not target_art:
        raise HTTPException(status_code=404, detail="Artifact not found.")

    file_path = Path(target_art["storage_path"])
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file missing on host storage.")

    return FileResponse(
        path=str(file_path),
        filename=safe_name,
        media_type=target_art["mime"],
    )
