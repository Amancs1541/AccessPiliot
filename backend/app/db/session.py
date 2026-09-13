import time
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services import server_health_state

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Real per-query timing for the System Health dashboard's DB panel (avg/slowest query) — nothing tracked this
# before. Event listeners attach to the underlying sync Engine (AsyncEngine wraps one internally via greenlet;
# this is the documented SQLAlchemy way to instrument it) and only ever append a float to a bounded in-memory
# deque, so the cost per query is one timestamp diff, never anything that touches the database itself.
# conn.info is a plain dict SQLAlchemy provides on the Connection specifically for this before/after pairing —
# used instead of keying by id(cursor), which risks a stale match if a freed cursor's id gets reused.
@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _record_query_start(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_times", []).append(time.perf_counter())


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _record_query_end(conn, cursor, statement, parameters, context, executemany):
    started_at = conn.info.get("query_start_times", []).pop() if conn.info.get("query_start_times") else None
    if started_at is not None:
        server_health_state.record_query_duration((time.perf_counter() - started_at) * 1000)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
