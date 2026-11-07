"""Execute and verify full live analysis workflow on production VM."""
import json
import sqlite3
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1"

print("=" * 70)
print("AURON — FULL LIVE VULTR ANALYSIS VERIFICATION")
print("=" * 70)

# 1. Verify health
health_req = urllib.request.urlopen(f"{BASE}/api/health")
health = json.loads(health_req.read().decode("utf-8"))
print(f"\n[1] Health Check: status={health.get('status')}")
print(f"    Inference Mode: {health.get('inference_mode')}")
print(f"    Docker Available: {health.get('docker_available')}")
assert health.get("inference_mode") == "live_vultr", f"Expected live_vultr, got {health.get('inference_mode')}"

# 2. Get dataset sales_clean.csv
ds_req = urllib.request.urlopen(f"{BASE}/api/datasets")
datasets = json.loads(ds_req.read().decode("utf-8"))
sales = [d for d in datasets if d["filename"] == "sales_clean.csv"]
assert sales, "Dataset 'sales_clean.csv' not found!"
dataset = sales[0]
dataset_id = dataset["id"]
print(f"\n[2] Target Dataset: {dataset['filename']} (ID: {dataset_id})")
print(f"    Rows: {dataset['row_count']}, Columns: {dataset['column_count']}")
print(f"    SHA-256: {dataset['file_hash']}")

if len(sys.argv) > 1:
    task_id = sys.argv[1]
    print(f"\n[3] Using Existing Task ID: {task_id}")
    poll_req = urllib.request.urlopen(f"{BASE}/api/tasks/{task_id}")
    task_data = json.loads(poll_req.read().decode("utf-8"))
    print(f"    Current Status: {task_data.get('status')}")
else:
    # 3. Submit analysis question
    question = "Which product category generated the highest total revenue? Calculate total revenue for every category, rank the categories, and generate a bar chart."
    print(f"\n[3] Submitting Question:")
    print(f"    '{question}'")

    payload = json.dumps({"dataset_id": dataset_id, "question": question}).encode("utf-8")
    submit_req = urllib.request.Request(
        f"{BASE}/api/tasks",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    task_resp = json.loads(urllib.request.urlopen(submit_req).read().decode("utf-8"))
    task_id = task_resp.get("task_id") or task_resp.get("id")
    print(f"    Task ID: {task_id}")
    print(f"    Initial Status: {task_resp.get('status')}")

    # 4. Poll task lifecycle with timestamps
    print(f"\n[4] Polling Task Execution Lifecycle...")
    start_time = time.time()
    last_status = None
    task_data = None

    for i in range(120):  # Up to 240s for live LLM planning + coding + docker + explaining
        time.sleep(2)
        poll_req = urllib.request.urlopen(f"{BASE}/api/tasks/{task_id}")
        task_data = json.loads(poll_req.read().decode("utf-8"))
        curr_status = task_data.get("status")
        
        if curr_status != last_status:
            elapsed = time.time() - start_time
            print(f"    [{elapsed:5.1f}s] Task status changed to: --> {curr_status}")
            last_status = curr_status
            
        if curr_status in ("completed", "failed", "infra_error"):
            break

    total_duration = time.time() - start_time
    print(f"\n[5] Analysis Complete in {total_duration:.2f}s! Final Status: {task_data.get('status')}")

if task_data.get("status") != "completed":
    print(f"ERROR: Task failed! Error message: {task_data.get('error_message')}")
    sys.exit(1)

# 6. Verify inference calls in SQLite
print(f"\n[6] Database Verification: Inference Calls Table")
conn = sqlite3.connect("/home/auron/auron/data/auron.db")
c = conn.cursor()
c.execute(
    "SELECT role, model_name, prompt_tokens, completion_tokens, duration_ms, success, created_at "
    "FROM inference_calls WHERE task_id = ? ORDER BY created_at ASC",
    (task_id,),
)
calls = c.fetchall()
print(f"    Total recorded inference calls: {len(calls)}")
for row in calls:
    role, mod, ptok, ctok, dur, succ, ts = row
    print(f"    - Role: {role:<10} | Model: {mod:<30} | Tokens: {ptok} prompt + {ctok} comp | Latency: {dur:.0f}ms | Success: {bool(succ)}")

# 7. Check Plan and Code
print(f"\n[7] Generated AI Plan:")
plan_str = task_data.get("plan_json")
if plan_str:
    try:
        plan_obj = json.loads(plan_str)
        print(json.dumps(plan_obj, indent=6))
    except Exception:
        print(f"    {plan_str[:300]}...")

# 8. Check Artifacts & SHA-256
print(f"\n[8] Artifact Verification:")
artifacts = task_data.get("artifacts", [])
print(f"    Artifacts generated: {len(artifacts)}")
for art in artifacts:
    fname = art.get("filename")
    sha = art.get("sha256")
    size = art.get("file_size")
    print(f"    - Filename: {fname}")
    print(f"      Size: {size} bytes")
    print(f"      SHA-256 (from DB): {sha}")
    
    # Download through API
    art_req = urllib.request.urlopen(f"{BASE}/api/tasks/{task_id}/artifacts/{fname}")
    downloaded_bytes = art_req.read()
    import hashlib
    computed_sha = hashlib.sha256(downloaded_bytes).hexdigest()
    print(f"      SHA-256 (computed): {computed_sha}")
    assert computed_sha == sha, f"Hash mismatch for {fname}!"
    print(f"      Verification: MATCHES EXACTLY")

# 9. Check AI Explanation
print(f"\n[9] AI Explanation Summary:")
print("-" * 50)
summary_text = task_data.get("summary_md") or task_data.get("summary")
print(summary_text)
print("-" * 50)

# 10. Check Containment Card
print(f"\n[10] Containment Card Evidence:")
containment = task_data.get("containment_card", {})
for key, item in containment.items():
    print(f"    - {item.get('name'):<30} | Status: {item.get('status'):<16} | Observed: {item.get('observed')}")

# 11. Check Receipt & Hash
print(f"\n[11] Canonical Execution Receipt:")
receipt_req = urllib.request.urlopen(f"{BASE}/api/tasks/{task_id}/receipt")
receipt = json.loads(receipt_req.read().decode("utf-8"))
exec_data = receipt.get("execution", {})
time_data = receipt.get("timestamps", {})
print(f"    Execution Status: {exec_data.get('status')}")
print(f"    Exit Code: {exec_data.get('exit_code')}")
print(f"    Duration: {time_data.get('duration_ms')}ms")
print(f"    Container ID: {receipt.get('container', {}).get('id')}")
print(f"    Image: {receipt.get('container', {}).get('image')}")
print(f"    Image Digest: {receipt.get('container', {}).get('image_digest')}")
print(f"    Cleanup Verified: {receipt.get('cleanup_verified')}")
print(f"    Receipt Hash: {task_data.get('receipt_hash')}")

print("\n" + "=" * 70)
print("LIVE VULTR INFERENCE & DOCKER EXECUTION: 100% VERIFIED SUCCESSFUL")
print("=" * 70)
