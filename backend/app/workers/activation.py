from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AccessAssignment
from app.services.assignments import grant_provider_access_for_assignment
from app.services.audit import record_audit

logger = logging.getLogger("accesspilot.activation")

POLL_INTERVAL_SECONDS = 60


def _is_due(assignment: AccessAssignment, now: datetime) -> bool:
    if assignment.status != "SCHEDULED":
        return False
    start_time = assignment.start_time
    if start_time is None:
        return True
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    return start_time <= now


async def activate_due_assignments(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Grants real Entra/Graph access for SCHEDULED assignments whose start_time has arrived. Returns the count activated."""
    async with session_factory() as session:
        candidates = list((await session.scalars(select(AccessAssignment).where(AccessAssignment.status == "SCHEDULED"))).all())
    now = datetime.now(timezone.utc)
    activated_count = 0
    for candidate in candidates:
        if not _is_due(candidate, now):
            continue
        async with session_factory() as session:
            assignment = await session.get(AccessAssignment, candidate.id)
            if assignment is None or not _is_due(assignment, now):
                continue
            granted = await grant_provider_access_for_assignment(session, assignment)
            if not granted:
                # Provider grant failed or is unverified — leave SCHEDULED and retry on the next poll rather than
                # falsely claiming access was granted (matches the documented provider-failure handling).
                await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, request_id=f"activation-worker-{assignment.id}", result="FAILURE")
                await session.commit()
                logger.warning("Provider grant failed for scheduled assignment %s; will retry", candidate.id)
                continue
            assignment.status = "ACTIVE"
            assignment.activated_at = now
            await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, request_id=f"activation-worker-{assignment.id}")
            await session.commit()
            activated_count += 1
    return activated_count


async def activation_worker_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            await activate_due_assignments(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Activation worker iteration failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
