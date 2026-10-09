from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import server_health_state
from app.services.workflows import sweep_workflow_escalations

logger = logging.getLogger("accesspilot.workflow_escalation")

POLL_INTERVAL_SECONDS = 60


async def workflow_escalation_worker_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Mirrors every other sweep worker's shape exactly: every 60s, escalates any workflow stage instance whose
    escalates_at threshold has passed and hasn't already been escalated, notifying its fallback approvers."""
    while True:
        try:
            async with session_factory() as session:
                await sweep_workflow_escalations(session)
            server_health_state.record_worker_tick("Workflow escalation worker", "ok")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Workflow escalation worker iteration failed")
            server_health_state.record_worker_tick("Workflow escalation worker", "crit")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
