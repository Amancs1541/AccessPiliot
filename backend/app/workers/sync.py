from __future__ import annotations


async def synchronize_provider(session, provider) -> dict[str, int]:
    """Worker entry point for provider synchronization; orchestration is deferred."""
    _ = session
    return await provider.sync()
