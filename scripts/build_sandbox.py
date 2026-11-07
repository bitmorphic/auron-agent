#!/usr/bin/env python3
"""Build the AURON sandbox Docker image."""

import subprocess
import sys
from pathlib import Path


def build(tag: str = "latest") -> str:
    """Build the sandbox image and return the full image name."""
    dockerfile_dir = Path(__file__).resolve().parent.parent / "sandbox"
    image_name = f"auron-sandbox:{tag}"

    print(f"[AURON] Building {image_name} from {dockerfile_dir} ...")
    result = subprocess.run(
        ["docker", "build", "-t", image_name, str(dockerfile_dir)],
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        print("Image build failed.", file=sys.stderr)
        sys.exit(1)

    print(f"[AURON] Image built: {image_name}")
    return image_name


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "latest"
    build(tag)

