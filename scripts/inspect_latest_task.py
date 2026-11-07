import json
import sqlite3

conn = sqlite3.connect('/home/auron/auron/data/auron.db')
cur = conn.cursor()
task = cur.execute('SELECT id, status, summary_md, error_message FROM tasks ORDER BY created_at DESC LIMIT 1').fetchone()
print(f"Task ID: {task[0]}")
print(f"Status: {task[1]}")

attempt = cur.execute('SELECT attempt_number, stdout, exit_code FROM attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1', (task[0],)).fetchone()
if attempt:
    print(f"Attempt: {attempt[0]} (exit_code: {attempt[2]})")
    print("\n--- STDOUT ---")
    print(attempt[1])

print("\n--- SUMMARY / EXPLANATION ---")
print(task[2])


