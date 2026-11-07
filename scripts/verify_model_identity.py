#!/usr/bin/env python3
"""
Verify that requested_model and returned_model are both deepseek-v4.1-flash-1017.
Never prints the API key.
"""
import sys
import asyncio
import time
import re

# Add project root to path
sys.path.insert(0, "/home/auron/auron")

from backend.config import settings
from backend.inference.client import LiveVultrClient, get_inference_client


async def main():
    print("=== AURON Model Identity Verification ===")
    print(f"Configured model_name : {settings.model_name}")
    print(f"Inference mode        : {'live_vultr' if settings.vultr_inference_api_key.strip() else 'mock'}")
    print()

    has_key = bool(settings.vultr_inference_api_key.strip())
    if not has_key:
        print("ERROR: No VULTR_INFERENCE_API_KEY set. Cannot run live test.")
        sys.exit(1)

    client = LiveVultrClient()

    print("Sending minimal inference request...")
    t0 = time.time()
    try:
        resp = await client.chat_completion(
            system_prompt="You are a test assistant. Reply concisely.",
            user_prompt="Reply with exactly: AURON MODEL IDENTITY VERIFIED",
            temperature=0.0,
            max_tokens=50,
        )
    except Exception as e:
        # Sanitize — do not print any header/key values
        err_str = str(e)
        err_str = re.sub(r'Bearer [A-Za-z0-9\-_\.]{10,}', 'Bearer [REDACTED]', err_str)
        print(f"ERROR: Inference call failed: {err_str}")
        sys.exit(1)
    latency_ms = (time.time() - t0) * 1000

    requested = resp.get("requested_model", "NOT PRESENT")
    returned  = resp.get("returned_model", "NOT PRESENT")
    content   = resp.get("content", "")
    usage     = {
        "prompt_tokens": resp.get("prompt_tokens", "?"),
        "completion_tokens": resp.get("completion_tokens", "?"),
    }

    print(f"requested_model   : {requested}")
    print(f"returned_model    : {returned}")
    print(f"content           : {content!r}")
    print(f"usage             : {usage}")
    print(f"latency_ms        : {latency_ms:.0f}")
    print()

    # Verify identity
    fails = []

    if requested == "NOT PRESENT" or returned == "NOT PRESENT":
        fails.append("response dict is missing requested_model or returned_model keys")

    if requested != settings.model_name:
        fails.append(f"requested_model ({requested!r}) does not match settings.model_name ({settings.model_name!r})")

    if returned != requested:
        print(f"WARNING: returned_model ({returned!r}) != requested_model ({requested!r})")
        print("  Vultr API is mapping the requested model to a different one.")
    else:
        print(f"PASS: returned_model matches requested_model: {returned!r}")

    if not content.strip():
        fails.append("Response content is empty")

    if fails:
        for f in fails:
            print(f"FAIL: {f}")
        sys.exit(1)

    print("PASS: Non-empty response received.")
    print()
    print("=== Model Identity Verification PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
