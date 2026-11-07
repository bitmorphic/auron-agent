#!/usr/bin/env python3
"""
Submit a full live analysis task via the AURON API, poll until completion,
and display results with both inference model info and sandbox receipt.
Never prints credentials or the API key.
"""
import sys
import time
import json
import os
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

BASE_URL = "http://127.0.0.1:8000"
SAMPLE_CSV = Path("/home/auron/auron/data/samples/sales_clean.csv")
QUESTION = (
    "Which product category generated the highest total revenue? "
    "Calculate total revenue for every category, rank the categories, "
    "and generate a bar chart."
)
MAX_WAIT_SECONDS = 300
POLL_INTERVAL = 5


def upload_dataset(client: httpx.Client) -> str:
    """Upload the sample CSV and return the dataset_id."""
    if not SAMPLE_CSV.exists():
        print(f"ERROR: Sample CSV not found at {SAMPLE_CSV}")
        sys.exit(1)

    print(f"Uploading {SAMPLE_CSV.name} ({SAMPLE_CSV.stat().st_size:,} bytes)...")
    with open(SAMPLE_CSV, "rb") as f:
        resp = client.post(
            f"{BASE_URL}/api/datasets/upload",
            files={"file": (SAMPLE_CSV.name, f, "text/csv")},
            timeout=30,
        )
    if resp.status_code not in (200, 201):
        print(f"ERROR: Upload failed HTTP {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)
    data = resp.json()
    dataset_id = data.get("dataset_id") or data.get("id")
    if not dataset_id:
        print(f"ERROR: Could not find dataset_id or id in response: {list(data.keys())}")
        sys.exit(1)
    print(f"  dataset_id : {dataset_id}")
    print(f"  rows       : {data.get('row_count', '?')}")
    print(f"  columns    : {data.get('column_count', '?')}")
    print(f"  sha256     : {str(data.get('file_hash', data.get('sha256', '?')))[:16]}...")
    return dataset_id


def submit_task(client: httpx.Client, dataset_id: str) -> str:
    """Submit analysis task and return task_id."""
    print(f"\nSubmitting analysis task...")
    payload = {"dataset_id": dataset_id, "question": QUESTION}
    resp = client.post(f"{BASE_URL}/api/tasks", json=payload, timeout=30)
    if resp.status_code not in (200, 201, 202):
        print(f"ERROR: Task submit failed HTTP {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)
    data = resp.json()
    task_id = data["task_id"]
    print(f"  task_id    : {task_id}")
    print(f"  status     : {data.get('status', '?')}")
    return task_id


def poll_task(client: httpx.Client, task_id: str) -> dict:
    """Poll task until terminal state. Returns final task detail dict."""
    terminal = {"completed", "failed", "error"}
    deadline = time.time() + MAX_WAIT_SECONDS
    last_status = None

    while time.time() < deadline:
        resp = client.get(f"{BASE_URL}/api/tasks/{task_id}", timeout=15)
        if resp.status_code != 200:
            print(f"  Warning: poll returned HTTP {resp.status_code}")
            time.sleep(POLL_INTERVAL)
            continue

        data = resp.json()
        status = data.get("status", "unknown")

        if status != last_status:
            ts = time.strftime("%H:%M:%S")
            print(f"  [{ts}] status: {status}")
            last_status = status

        if status in terminal:
            return data

        time.sleep(POLL_INTERVAL)

    print(f"ERROR: Task did not complete within {MAX_WAIT_SECONDS}s")
    sys.exit(1)


def print_receipt(client: httpx.Client, task_id: str):
    """Print the execution receipt."""
    resp = client.get(f"{BASE_URL}/api/tasks/{task_id}/receipt", timeout=15)
    if resp.status_code != 200:
        print(f"  Warning: receipt returned HTTP {resp.status_code}")
        return
    receipt = resp.json()
    print("\n--- Execution Receipt ---")
    print(json.dumps(receipt, indent=2, default=str))


def main():
    print("=== AURON Full Live Analysis Runner ===")
    print(f"Question: {QUESTION[:80]}...")
    print()

    with httpx.Client(base_url=BASE_URL) as client:
        # Verify service is live
        health = client.get("/api/health", timeout=10).json()
        print(f"Health: {health.get('status')} | inference: {health.get('inference_mode')} | model: {health.get('model_name')}")
        print()

        dataset_id = upload_dataset(client)
        task_id = submit_task(client, dataset_id)

        print(f"\nPolling (up to {MAX_WAIT_SECONDS}s)...")
        result = poll_task(client, task_id)

        status = result.get("status")
        print(f"\nFinal status: {status}")

        if status == "completed":
            print(f"\nrequested_model : {result.get('requested_model', 'N/A')}")
            print(f"returned_model  : {result.get('returned_model', 'N/A')}")
            print(f"\nStdout (first 2000 chars):")
            stdout = result.get("stdout", "") or ""
            print(stdout[:2000])
            artifacts = result.get("artifacts", [])
            print(f"\nArtifacts: {[a.get('filename') for a in artifacts]}")
            explanation = result.get("explanation", "") or ""
            print(f"\nExplanation (first 1500 chars):")
            print(explanation[:1500])
        else:
            print(f"Error: {result.get('error_message', 'No error message')}")

        print_receipt(client, task_id)

    print("\n=== Analysis Complete ===")


if __name__ == "__main__":
    main()
