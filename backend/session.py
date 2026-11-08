"""
AURON Anonymous Workspace Session Layer.
Issues cryptographically secure random session tokens (256 bits),
stores only SHA-256 token hashes in SQLite, and provides session
validation, expiration, revocation, and FastAPI dependency/middleware.
Source of Truth: PRD v3.0 §13 & §14.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request, Response, status

from backend.config import settings
from backend.database import create_session, get_session_by_token_hash

logger = logging.getLogger("auron.session")

COOKIE_NAME = "auron_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days


def generate_session_token() -> str:
    """Generate a 256-bit entropy cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Compute canonical SHA-256 digest of session token for storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_session_from_request(request: Request) -> Optional[dict]:
    """
    Extract and validate session from request cookie.
    Returns session dict if active and valid, else None.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        # Also check Authorization header Bearer token for automated API clients/tests
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

    if not token:
        return None

    token_hash = hash_session_token(token)
    session = get_session_by_token_hash(token_hash)
    return session


def get_or_create_session(request: Request) -> tuple[str, Optional[str]]:
    """
    Get existing active session ID or create a new anonymous session.
    Returns: (session_id, raw_token_if_new_else_none).
    """
    existing = get_session_from_request(request)
    if existing:
        return existing["id"], None

    # Mint fresh anonymous session
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    new_session = create_session(token_hash=token_hash, expires_days=30)
    return new_session["id"], raw_token


def set_session_cookie(
    response: Response,
    token: str,
    request: Optional[Request] = None,
) -> None:
    """
    Set Secure, HttpOnly, SameSite=Lax cookie on response.
    Never exposes token in body or JavaScript.
    """
    # In production behind Caddy/HTTPS, X-Forwarded-Proto is https.
    # In tests or local HTTP, avoid setting secure=True so test client retains cookies.
    is_secure = False
    if request:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).lower()
        if proto == "https":
            is_secure = True
    elif not settings.debug:
        is_secure = True

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )


async def get_current_session_id(request: Request) -> str:
    """
    FastAPI dependency to retrieve the active session ID.
    Always returns a valid session ID (from request state or freshly minted).
    """
    session_id = getattr(request.state, "session_id", None)
    if session_id:
        return session_id

    # Fallback if accessed outside middleware
    session_id, _ = get_or_create_session(request)
    request.state.session_id = session_id
    return session_id
