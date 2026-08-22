from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text


async def expire_active_assignments(session, provider) -> int:
    """Worker entry point for the future transactional expiration job."""
    result = await session.execute(text("SELECT 1"))
    _ = result, provider, datetime.now(timezone.utc)
    return 0
