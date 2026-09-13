from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import server_health_state
from app.services.sod import revoke_lapsed_sod_exceptions

logger = logging.getLogger("accesspilot.sod_expiry")

POLL_INTERVAL_SECONDS = 60


async def sod_exception_expiry_worker_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Mirrors expiration_worker_loop's shape: every 60s, ends whatever real/eligible access an SoD exception was
    specifically granted to cover, once that exception's expires_at has passed — regardless of who (if anyone) has
    the app open, unlike the notify-only reconciliation pass in services/sod.py which only runs when a signed-in
    Admin/SoDAdmin's browser happens to poll GET /sod/notifications."""
    while True:
        try:
            async with session_factory() as session:
                await revoke_lapsed_sod_exceptions(session)
            server_health_state.record_worker_tick("SoD exception expiry worker", "ok")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("SoD exception expiry worker iteration failed")
            server_health_state.record_worker_tick("SoD exception expiry worker", "crit")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
