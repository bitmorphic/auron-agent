import sqlite3

conn = sqlite3.connect('/home/auron/auron/data/auron.db')
cur = conn.cursor()
rows = cur.execute('SELECT role, model_name, requested_model, returned_model, prompt_tokens, completion_tokens, duration_ms FROM inference_calls ORDER BY created_at DESC LIMIT 6').fetchall()
print("Role | Model Name | Requested Model | Returned Model | Prompt Tokens | Completion Tokens | Duration (ms)")
print("-" * 95)
for r in rows:
    print(f"{r[0]:<10} | {r[1]:<25} | {r[2]:<25} | {r[3]:<25} | {r[4]:<5} | {r[5]:<5} | {r[6]:.1f}")

