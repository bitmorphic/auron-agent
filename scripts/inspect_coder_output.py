"""Inspect last task and calls."""
import sqlite3
import json

conn = sqlite3.connect("/home/auron/auron/data/auron.db")
c = conn.cursor()
c.execute("SELECT id, status, plan_json, final_code, error_message FROM tasks ORDER BY created_at DESC LIMIT 1")
row = c.fetchone()
if row:
    tid, st, plan, code, err = row
    print("Task ID:", tid)
    print("Status:", st)
    print("Error:", err)
    print("Plan length:", len(plan) if plan else 0)
    print("Code length:", len(code) if code else 0)

c.execute("SELECT role, model_name, prompt_tokens, completion_tokens, duration_ms, success, error_message FROM inference_calls WHERE task_id = ?", (tid,))
calls = c.fetchall()
print(f"Calls for task {tid}:")
for call in calls:
    print(" ", call)
