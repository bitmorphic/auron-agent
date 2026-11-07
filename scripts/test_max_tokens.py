"""Test max_tokens 4096 with Vultr Serverless Inference.
"""
import os
import sys
import json
import urllib.request

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

payload = json.dumps({
    "model": settings.model_name,
    "messages": [
        {"role": "user", "content": "Write a short Python function to calculate fibonacci numbers."}
    ],
    "max_tokens": 4096,
    "temperature": 0.2,
}).encode("utf-8")

req = urllib.request.Request(
    "https://api.vultrinference.com/v1/chat/completions",
    data=payload,
    headers=headers,
)
with urllib.request.urlopen(req, timeout=30) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print("Status:", resp.status)
    print("Model:", data.get("model"))
    print("Usage:", data.get("usage"))
    print("Output preview:", data.get("choices", [{}])[0].get("message", {}).get("content")[:100])
