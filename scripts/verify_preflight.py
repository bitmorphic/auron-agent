"""Pre-flight check script for Vultr inference verification.
NEVER PRINTS THE API KEY OR SECRETS.
"""
import os
import sys
import json
import urllib.request
import urllib.error

# Ensure app path
sys.path.insert(0, "/home/auron/auron")

# Load environment from /etc/auron/env if not already set
env_path = "/etc/auron/env"
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

from backend.config import settings

print("=== [PRE-FLIGHT CHECK 1: Configuration] ===")
print(f"Base URL: {settings.vultr_inference_base_url}")
print(f"Configured Model ID: {settings.model_name}")
api_key = settings.vultr_inference_api_key
has_key = bool(api_key and len(api_key.strip()) > 0)
print(f"API Key present: {has_key}")
print(f"API Key length: {len(api_key.strip()) if has_key else 0} chars")

if not has_key:
    print("ERROR: VULTR_INFERENCE_API_KEY is not set or empty!")
    sys.exit(1)

print("\n=== [PRE-FLIGHT CHECK 2: Live Vultr Model Catalogue] ===")
models_url = f"{settings.vultr_inference_base_url.rstrip('/')}/models"
print(f"Querying models endpoint: {models_url}")

headers = {
    "Authorization": f"Bearer {api_key.strip()}",
    "Content-Type": "application/json",
}

req = urllib.request.Request(models_url, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        status_code = resp.status
        body = json.loads(resp.read().decode("utf-8"))
        print(f"Models endpoint status: {status_code} OK")
        
        # Check available models
        data = body.get("data", [])
        model_ids = [m.get("id") for m in data if isinstance(m, dict)]
        print(f"Total models available: {len(model_ids)}")
        print(f"Available model IDs:")
        for mid in sorted(model_ids):
            print(f"  - {mid}")
            
        is_target_available = settings.model_name in model_ids
        print(f"\nConfigured model '{settings.model_name}' available: {is_target_available}")
        if not is_target_available:
            print(f"WARNING: '{settings.model_name}' was not found in the live catalogue!")
        else:
            print(f"CONFIRMED: Model '{settings.model_name}' is active and ready.")
except urllib.error.HTTPError as e:
    print(f"ERROR querying models catalogue: HTTP {e.code} - {e.reason}")
    sys.exit(2)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    sys.exit(3)
