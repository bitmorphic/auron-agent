"""
AURON Rate Limiting, Concurrency Guard & Request Validation.
Provides per-session sliding-window rate limiting, global concurrency
safeguards against inference exhaust, and CSRF/Origin validation.
Source of Truth: PRD v3.0 §10, §14 & Release Lockdown.
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request, status

from backend.config import settings

logger = logging.getLogger("auron.rate_limit")

# ─── Sliding Window Rate Limiter ──────────────────────────────────────────────

# Key: (session_id, action) -> list of timestamp floats
_rate_limits: dict[tuple[str, str], list[float]] = defaultdict(list)
_rate_lock = asyncio.Lock()


async def check_rate_limit(
    session_id: str,
    action: str,
    max_requests: int = 15,
    window_seconds: int = 60,
) -> None:
    """
    Enforce a sliding-window rate limit per session.
    Raises HTTP 429 Too Many Requests if ceiling is exceeded.
    """
    now = time.time()
    cutoff = now - window_seconds
    key = (session_id, action)

    async with _rate_lock:
        timestamps = _rate_limits[key]
        # Prune expired timestamps
        _rate_limits[key] = [t for t in timestamps if t > cutoff]
        if len(_rate_limits[key]) >= max_requests:
            logger.warning(
                "Rate limit exceeded for session %s on action %s (%d requests in %ds)",
                session_id, action, len(_rate_limits[key]), window_seconds,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {action}. Please wait before making further requests.",
            )
        _rate_limits[key].append(now)


# ─── Global Concurrency Guard ─────────────────────────────────────────────────

# Bounded concurrency semaphore across all visitors to safeguard Vultr inference budget and Docker resources
_global_task_semaphore: Optional[asyncio.Semaphore] = None


def get_global_task_semaphore() -> asyncio.Semaphore:
    """Return the global task execution semaphore."""
    global _global_task_semaphore
    if _global_task_semaphore is None:
        _global_task_semaphore = asyncio.Semaphore(settings.max_concurrent_sandboxes)
    return _global_task_semaphore


# ─── Origin & Referer Validation ──────────────────────────────────────────────


def validate_request_origin(request: Request) -> None:
    """
    Validate Origin/Referer headers on state-changing requests (POST, PUT, DELETE)
    to defend against cross-site request forgery.
    """
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")

    # If neither Origin nor Referer is provided (e.g., standard CLI curl or API test client), allow
    if not origin and not referer:
        return

    # Check host header
    host = request.headers.get("host", "").split(":")[0].lower()
    allowed_hosts = {
        host,
        "localhost",
        "127.0.0.1",
        "testserver",
        "45.77.19.39",
        "45.77.19.39.sslip.io",
    }

    if origin:
        try:
            from urllib.parse import urlparse
            origin_host = urlparse(origin).hostname
            if origin_host and origin_host.lower() not in allowed_hosts:
                logger.warning("Rejected cross-origin request from origin: %s", origin)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cross-origin request rejected.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    if referer and not origin:
        try:
            from urllib.parse import urlparse
            ref_host = urlparse(referer).hostname
            if ref_host and ref_host.lower() not in allowed_hosts:
                logger.warning("Rejected cross-origin request from referer: %s", referer)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cross-origin referer rejected.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

