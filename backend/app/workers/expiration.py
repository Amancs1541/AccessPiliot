from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AccessAssignment
from app.services.assignments import revoke_provider_access
from app.services.audit import record_audit

logger = logging.getLogger("accesspilot.expiration")

POLL_INTERVAL_SECONDS = 60


def _is_expired(assignment: AccessAssignment, now: datetime) -> bool:
    if assignment.status != "ACTIVE" or assignment.expiration_time is None:
        return False
    expiration_time = assignment.expiration_time
    if expiration_time.tzinfo is None:
        expiration_time = expiration_time.replace(tzinfo=timezone.utc)
    return expiration_time <= now


async def expire_due_assignments(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Transitions ACTIVE, time-bound assignments past their expiration_time to EXPIRED. Returns the count expired."""
    async with session_factory() as session:
        candidates = list((await session.scalars(select(AccessAssignment).where(AccessAssignment.status == "ACTIVE", AccessAssignment.expiration_time.isnot(None)))).all())
    now = datetime.now(timezone.utc)
    expired_count = 0
    for candidate in candidates:
        if not _is_expired(candidate, now):
            continue
        async with session_factory() as session:
            assignment = await session.get(AccessAssignment, candidate.id)
            if assignment is None or not _is_expired(assignment, now):
                continue
            revoked = await revoke_provider_access(session, assignment)
            if not revoked:
                # Provider removal failed or is unverified — leave ACTIVE and retry on the next poll rather than
                # falsely claiming the access was removed (matches the documented provider-failure handling).
                await record_audit(session, action="ASSIGNMENT_EXPIRED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, request_id=f"expiration-worker-{assignment.id}", result="FAILURE")
                await session.commit()
                logger.warning("Provider revoke failed for assignment %s; will retry", candidate.id)
                continue
            try:
                assignment.status = "EXPIRED"
                await record_audit(session, action="ASSIGNMENT_EXPIRED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, request_id=f"expiration-worker-{assignment.id}")
                await session.commit()
                expired_count += 1
            except Exception:
                await session.rollback()
                logger.exception("Failed to expire assignment %s", candidate.id)
    return expired_count


async def expiration_worker_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            await expire_due_assignments(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Expiration worker iteration failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
