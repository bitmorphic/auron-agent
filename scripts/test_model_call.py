"""Test chat completions for model.
NEVER PRINTS THE API KEY.
"""
import os
import sys
import json
import urllib.request
import urllib.error

sys.path.insert(0, "/home/auron/auron")

env_path = "/etc/auron/env"
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

from backend.config import settings

api_key = settings.vultr_inference_api_key
headers = {
    "Authorization": f"Bearer {api_key.strip()}",
    "Content-Type": "application/json",
}

def test_model(model_name: str):
    print(f"\n--- Testing chat completion with model: '{model_name}' ---")
    payload = json.dumps({
        "model": model_name,
        "messages": [
            {"role": "user", "content": "Reply with exactly: AURON LIVE INFERENCE WORKS"}
        ],
        "max_tokens": 50,
        "temperature": 0.0,
    }).encode("utf-8")
    
    req = urllib.request.Request(
        "https://api.vultrinference.com/v1/chat/completions",
        data=payload,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("Status: 200 OK")
            print("Response ID:", data.get("id"))
            print("Returned Model:", data.get("model"))
            print("Content:", data.get("choices", [{}])[0].get("message", {}).get("content"))
            print("Usage:", data.get("usage"))
            return True, data
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} Error: {err_msg}")
        return False, err_msg
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}")
        return False, str(e)

# Test configured model first
test_model(settings.model_name)
