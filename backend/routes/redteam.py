"""
AURON Red Team API Routes
Endpoints to query available probes, execute bounded security probes,
and retrieve audit run history.

Source of Truth: PRD v3.0 §11 & §14
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel

from backend.config import settings
from backend.database import list_redteam_runs, get_redteam_run
from backend.redteam.engine import RedTeamEngine, AVAILABLE_PROBES

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

    # If configured with a token, require exact match
    if expected and provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Red Team authorization token.",
        )


class RunProbeRequest(BaseModel):
    token: Optional[str] = None


@router.get("/probes")
async def list_probes() -> dict[str, Any]:
    """List available bounded security probes."""
    return {
        "enabled": settings.red_team_enabled,
        "probes": list(AVAILABLE_PROBES.values()),
    }


@router.post("/{probe_id}/run")
async def run_probe_endpoint(
    probe_id: str,
    req: Optional[RunProbeRequest] = None,
    x_redteam_token: Optional[str] = Header(None, alias="X-RedTeam-Token"),
    token: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Execute a single bounded probe against a disposable test container."""
    body_token = req.token if req else None
    _verify_redteam_access(
        header_token=x_redteam_token,
        query_token=token,
        body_token=body_token,
    )

    if probe_id not in AVAILABLE_PROBES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Probe '{probe_id}' not found. Available: {list(AVAILABLE_PROBES.keys())}",
        )

    engine = get_engine()
    try:
        result = engine.run_probe(probe_id)
        return result
    except Exception as e:
        logger.exception("Red Team probe execution error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Probe execution error: {type(e).__name__}: {str(e)}",
        )


@router.get("/runs")
async def get_runs_endpoint(
    limit: int = Query(20, ge=1, le=100),
    x_redteam_token: Optional[str] = Header(None, alias="X-RedTeam-Token"),
    token: Optional[str] = Query(None),
) -> list[dict[str, Any]]:
    """Retrieve audit history of Red Team runs."""
    _verify_redteam_access(header_token=x_redteam_token, query_token=token)
    return list_redteam_runs(limit=limit)


@router.get("/runs/{run_id}")
async def get_single_run_endpoint(
    run_id: str,
    x_redteam_token: Optional[str] = Header(None, alias="X-RedTeam-Token"),
    token: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Retrieve details and evidence for a specific Red Team run."""
    _verify_redteam_access(header_token=x_redteam_token, query_token=token)
    run = get_redteam_run(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Red Team run '{run_id}' not found.",
        )
    return run

