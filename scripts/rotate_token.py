#!/usr/bin/env python3
"""
Securely rotate RED_TEAM_TOKEN in /etc/auron/env and set RED_TEAM_ENABLED=false.
Never prints the secret token.
"""
import os
import re
import secrets
import subprocess
import sys

ENV_FILE = "/etc/auron/env"

def main():
    if not os.path.exists(ENV_FILE):
        print(f"ERROR: {ENV_FILE} not found.")
        sys.exit(1)

    # Read current lines
    try:
        res = subprocess.run(["sudo", "cat", ENV_FILE], capture_output=True, text=True, check=True)
        content = res.stdout
    except Exception as e:
        print(f"ERROR reading env file: {e}")
        sys.exit(1)

    # Generate strong random token (32 bytes = 256 bits entropy)
    new_token = secrets.token_urlsafe(32)

    # Filter out existing RED_TEAM_ lines
    lines = [line for line in content.splitlines() if not line.startswith("RED_TEAM_")]

    # Append new disabled setting and token
    lines.append("RED_TEAM_ENABLED=false")
    lines.append(f"RED_TEAM_TOKEN={new_token}")
    lines.append("")  # trailing newline

    new_content = "\n".join(lines)

    # Write back via sudo tee
    proc = subprocess.Popen(["sudo", "tee", ENV_FILE], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    _, err = proc.communicate(input=new_content)
    if proc.returncode != 0:
        print(f"ERROR writing {ENV_FILE}: {err}")
        sys.exit(1)

    # Ensure permissions remain 0640
    subprocess.run(["sudo", "chmod", "0640", ENV_FILE], check=True)
    subprocess.run(["sudo", "chown", "root:auron", ENV_FILE], check=True)

    print("SUCCESS: RED_TEAM_TOKEN rotated to strong random 256-bit secret.")
    print("SUCCESS: RED_TEAM_ENABLED set to false.")
    print(f"SUCCESS: {ENV_FILE} permissions verified (0640, root:auron).")

if __name__ == "__main__":
    main()

