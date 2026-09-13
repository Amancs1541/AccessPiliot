from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.server_health import ServerHealthResponse
from app.schemas.troubleshooting import TroubleshootingResponse
from app.security.auth import AuthenticatedUser, require_permission
from app.services.server_health import get_live_server_health
from app.services.troubleshooting import build_troubleshooting_report

router = APIRouter(prefix="/server-health", tags=["server-health"])
server_health_read = require_permission("SERVER_HEALTH_READ")


@router.get("", response_model=ServerHealthResponse)
async def get_server_health(_: AuthenticatedUser = Depends(server_health_read), db: AsyncSession = Depends(get_db)):
    """V2: real data from real sources (see services/server_health.py) — only AccessPilot.ServerAdmin can reach
    this at all."""
    return await get_live_server_health(db)


@router.get("/troubleshooting", response_model=TroubleshootingResponse)
async def get_troubleshooting_report(_: AuthenticatedUser = Depends(server_health_read), db: AsyncSession = Depends(get_db)):
    """Same permission as the main dashboard — a drill-down, not a separate surface. See
    services/troubleshooting.py for exactly which signals feed the incident/root-cause/checklist logic."""
    return await build_troubleshooting_report(db)
