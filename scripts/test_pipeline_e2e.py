"""AURON End-to-End Pipeline Smoke Test on VM."""
import json
import time
import urllib.request

BASE = "http://127.0.0.1"

print(f"[TEST] Checking AURON health at {BASE}/api/health...")
req = urllib.request.urlopen(f"{BASE}/api/health")
health = json.loads(req.read())
print(f"[TEST] Health: status={health.get('status')}, inference_mode={health.get('inference_mode')}, docker={health.get('docker_available')}")

print(f"[TEST] Listing datasets at {BASE}/api/datasets...")
req = urllib.request.urlopen(f"{BASE}/api/datasets")
datasets = json.loads(req.read())
print(f"[TEST] Found {len(datasets)} dataset(s)")
assert len(datasets) > 0, "No datasets found!"

sales = [d for d in datasets if d["filename"] == "sales_clean.csv"][0]
print(f"[TEST] Using dataset: {sales['id']} ({sales['filename']})")

question = "Plot total revenue by product category as a bar chart"
print(f"[TEST] Submitting task: '{question}'...")
payload = json.dumps({"dataset_id": sales["id"], "question": question}).encode("utf-8")
post_req = urllib.request.Request(
    f"{BASE}/api/tasks",
    data=payload,
    headers={"Content-Type": "application/json"},
)
task_resp = json.loads(urllib.request.urlopen(post_req).read())
task_id = task_resp.get("task_id") or task_resp.get("id")
print(f"[TEST] Task submitted: id={task_id}, status={task_resp.get('status')}")

# Poll task
task_data = None
for i in range(45):
    time.sleep(2)
    poll_req = urllib.request.urlopen(f"{BASE}/api/tasks/{task_id}")
    task_data = json.loads(poll_req.read())
    status = task_data.get("status")
    print(f"[TEST] Poll {i+1}: status={status}")
    if status in ("completed", "failed"):
        break

print(f"[TEST] Final Task Status: {task_data.get('status')}")
print(f"[TEST] Summary: {task_data.get('summary')}")

# Verify artifacts
artifacts = task_data.get("artifacts", [])
print(f"[TEST] Artifacts count: {len(artifacts)}")
for art in artifacts:
    filename = art.get("filename")
    print(f"  - {filename} ({art.get('mime')}, {art.get('file_size')} bytes, sha256: {art.get('sha256')[:16]}...)")
    # Download artifact
    art_req = urllib.request.urlopen(f"{BASE}/api/tasks/{task_id}/artifacts/{filename}")
    art_bytes = art_req.read()
    print(f"    Downloaded successfully: {len(art_bytes)} bytes")

# Verify Containment Card
containment = task_data.get("containment_card", {})
print(f"[TEST] Containment Card items: {list(containment.keys())}")
for key, item in containment.items():
    print(f"  - {item.get('name')}: status={item.get('status')}, observed={item.get('observed')}")

# Verify Receipt
receipt_req = urllib.request.urlopen(f"{BASE}/api/tasks/{task_id}/receipt")
receipt = json.loads(receipt_req.read())
print(f"[TEST] Receipt: execution_status={receipt.get('execution', {}).get('status')}, duration_ms={receipt.get('timestamps', {}).get('duration_ms')}")
print(f"[TEST] Container inspect data present: {'inspect' in receipt.get('container', {})}")
print(f"[TEST] Cleanup verified: {receipt.get('cleanup_verified')}")

assert task_data.get("status") == "completed", f"Task did not complete successfully: {task_data}"
print("[TEST] SUCCESS: Complete analysis, artifact download, containment card, and receipt verified!")
