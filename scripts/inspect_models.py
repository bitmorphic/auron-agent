"""Inspect Vultr catalogue for llama and deepseek."""
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

req = urllib.request.Request("https://api.vultrinference.com/v1/models", headers=headers)
with urllib.request.urlopen(req, timeout=15) as resp:
    data = json.loads(resp.read().decode("utf-8")).get("data", [])
    model_ids = sorted([m.get("id") for m in data if isinstance(m, dict)])
    
    print(f"Total models in catalogue: {len(model_ids)}")
    llama_models = [m for m in model_ids if "llama" in m.lower()]
    print(f"Llama models present: {llama_models}")
    print(f"'llama-3.3-70b-instruct-fp8' present: {'llama-3.3-70b-instruct-fp8' in model_ids}")
    print(f"'deepseek-v4.1-flash-1017' present: {'deepseek-v4.1-flash-1017' in model_ids}")
    print("\nComplete catalogue list:")
    for m in model_ids:
        print(f"  - {m}")
