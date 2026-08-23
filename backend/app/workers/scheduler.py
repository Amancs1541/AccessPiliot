from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import IdentityProvider
from app.services.directory_sync import run_sync

logger = logging.getLogger("accesspilot.scheduler")

POLL_INTERVAL_SECONDS = 60


def _is_due(provider: IdentityProvider, now: datetime) -> bool:
    if not provider.sync_interval_minutes or provider.sync_interval_minutes <= 0:
        return False
    if provider.last_sync_at is None:
        return True
    last_sync_at = provider.last_sync_at
    if last_sync_at.tzinfo is None:
        last_sync_at = last_sync_at.replace(tzinfo=timezone.utc)
    elapsed_minutes = (now - last_sync_at).total_seconds() / 60
    return elapsed_minutes >= provider.sync_interval_minutes


async def run_due_syncs(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Runs sync for every provider whose schedule is due. Returns the count of syncs attempted."""
    attempted = 0
    async with session_factory() as session:
        providers = list((await session.scalars(select(IdentityProvider).where(IdentityProvider.sync_interval_minutes.isnot(None)))).all())
    now = datetime.now(timezone.utc)
    for provider in providers:
        if not _is_due(provider, now):
            continue
        attempted += 1
        async with session_factory() as session:
            live_provider = await session.get(IdentityProvider, provider.id)
            if live_provider is None:
                continue
            try:
                await run_sync(session, live_provider, f"scheduled-sync-{live_provider.id}")
            except Exception:
                logger.exception("Scheduled sync failed for provider %s", live_provider.id)
    return attempted


async def sync_scheduler_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            await run_due_syncs(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sync scheduler iteration failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
