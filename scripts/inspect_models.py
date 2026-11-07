"""Inspect model metadata from Vultr catalogue.
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

req = urllib.request.Request("https://api.vultrinference.com/v1/models", headers=headers)
with urllib.request.urlopen(req, timeout=15) as resp:
    data = json.loads(resp.read().decode("utf-8")).get("data", [])
    for m in data:
        print(f"ID: {m.get('id'):<35} | Object: {m.get('object')} | Owned By: {m.get('owned_by')}")
