from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import server_health_state
from app.services.access_reviews import sweep_overdue_campaigns, sweep_scheduled_campaigns

logger = logging.getLogger("accesspilot.access_review")

POLL_INTERVAL_SECONDS = 60


async def access_review_worker_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Mirrors sod_expiry.py's exact shape: every 60s, closes any ACTIVE access review campaign whose due_at has
    passed, auto-revoking every still-PENDING item's real access along the way — regardless of who (if anyone)
    has the app open."""
    while True:
        try:
            async with session_factory() as session:
                await sweep_overdue_campaigns(session)
                await sweep_scheduled_campaigns(session)
            server_health_state.record_worker_tick("Access review worker", "ok")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Access review worker iteration failed")
            server_health_state.record_worker_tick("Access review worker", "crit")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
