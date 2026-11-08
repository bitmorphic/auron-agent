"""
AURON Red Team API Routes
Endpoints to query available probes, execute bounded security probes,
and retrieve audit run history (scoped to current session).

Source of Truth: PRD v3.0 §11 & §14
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel

from backend.config import settings
from backend.database import list_redteam_runs, get_redteam_run
from backend.rate_limit import check_rate_limit
from backend.redteam.engine import RedTeamEngine, AVAILABLE_PROBES
from backend.session import get_current_session_id

logger = logging.getLogger("auron.routes.redteam")
router = APIRouter(prefix="/api/redteam", tags=["Red Team Arena"])

# Singleton engine
_engine: Optional[RedTeamEngine] = None


def get_engine() -> RedTeamEngine:
    global _engine
    if _engine is None:
        _engine = RedTeamEngine()
    return _engine


def _verify_redteam_access(
    header_token: Optional[str] = None,
    query_token: Optional[str] = None,
    body_token: Optional[str] = None,
) -> None:
    """Verify feature flag and authentication token for Red Team operations."""
    if not settings.red_team_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Red Team Arena is disabled on this server.",
        )

    provided = header_token or query_token or body_token or ""
    expected = settings.red_team_token.strip()

    # If configured with a token, require exact match using constant-time comparison
    import secrets
    if expected and not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Red Team authorization token.",
        )


class RunProbeRequest(BaseModel):
    token: Optional[str] = None


@router.get("/probes")
async def list_probes() -> dict[str, Any]:
    """List available bounded security probes. Public endpoint."""
    return {
        "enabled": settings.red_team_enabled,
        "probes": list(AVAILABLE_PROBES.values()),
    }


@router.post("/{probe_id}/run")
async def run_probe_endpoint(
    probe_id: str,
    request: Request,
    req: Optional[RunProbeRequest] = None,
    x_redteam_token: Optional[str] = Header(None, alias="X-RedTeam-Token"),
    token: Optional[str] = Query(None),
    session_id: str = Depends(get_current_session_id),
) -> dict[str, Any]:
    """Execute a single bounded probe against a disposable test container."""
    body_token = req.token if req else None
    _verify_redteam_access(
        header_token=x_redteam_token,
        query_token=token,
        body_token=body_token,
    )

    # Rate limit Red Team probe execution
    await check_rate_limit(session_id, "redteam_probe", max_requests=5, window_seconds=60)

    if probe_id not in AVAILABLE_PROBES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Probe '{probe_id}' not found. Available: {list(AVAILABLE_PROBES.keys())}",
        )

    engine = get_engine()
    try:
        result = engine.run_probe(probe_id, owner_session_id=session_id)
        return result
    except Exception as e:
        logger.exception("Red Team probe execution error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Probe execution error: {type(e).__name__}: {str(e)}",
        )


@router.get("/runs")
async def list_runs(
    limit: int = Query(50, ge=1, le=100),
    x_redteam_token: Optional[str] = Header(None, alias="X-RedTeam-Token"),
    token: Optional[str] = Query(None),
    session_id: str = Depends(get_current_session_id),
) -> list[dict[str, Any]]:
    """List recent Red Team probe runs for the current session."""
    _verify_redteam_access(header_token=x_redteam_token, query_token=token)
    return list_redteam_runs(limit=limit, owner_session_id=session_id)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    x_redteam_token: Optional[str] = Header(None, alias="X-RedTeam-Token"),
    token: Optional[str] = Query(None),
    session_id: str = Depends(get_current_session_id),
) -> dict[str, Any]:
    """Get a specific Red Team run by ID (must belong to current session)."""
    _verify_redteam_access(header_token=x_redteam_token, query_token=token)
    run = get_redteam_run(run_id, owner_session_id=session_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run
