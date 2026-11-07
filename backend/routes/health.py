"""
AURON Health Route
PRD §6 FR-13: reports application version, image tag, Docker reachability,
inference status. Never discloses secrets, internal paths, or env vars.
"""

import subprocess
from fastapi import APIRouter
from backend.config import settings
from sandbox.runner import _find_docker

router = APIRouter(tags=["Health"])


@router.get("/api/health")
@router.get("/health")
async def health_check() -> dict:
    """Public health endpoint."""
    docker_ok = False
    try:
        docker_bin = _find_docker()
        res = subprocess.run([docker_bin, "info"], capture_output=True, timeout=5)
        docker_ok = (res.returncode == 0)
    except Exception:
        docker_ok = False

    has_live_key = bool(settings.vultr_inference_api_key.strip())

    return {
        "status": "ok",
        "version": "0.1.0",
        "docker_available": docker_ok,
        "inference_mode": "live_vultr" if has_live_key else "mock",
        "model_name": settings.model_name,
        "sandbox_image_tag": settings.sandbox_image_tag,
    }

