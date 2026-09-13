from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Optional

# When this process actually started — real, set once at import time (this module is imported very early, by
# db/session.py, well before the app starts serving). Backs the API card's "process uptime" metric; there's no
# persisted request-history table to compute a real 30-day uptime percentage from, so this is the honest
# alternative rather than a fabricated number.
PROCESS_STARTED_AT = datetime.now(timezone.utc)

# Process-local, in-memory only — resets on restart, which is fine for an ops health page (it's describing "what
# is this process doing right now", not something that needs to survive a restart). Nothing here is persisted to
# the database; this is deliberately separate from every other piece of app state, which all lives in Postgres.

# Every request the ASGI timing middleware (main.py) has seen recently, newest last. Bounded so memory can never
# grow unbounded on a long-running process — old entries fall off as new ones arrive.
REQUEST_LOG: deque = deque(maxlen=5000)

# Every SQLAlchemy query duration (ms) recorded by the engine-level event listeners (db/session.py).
QUERY_DURATIONS: deque = deque(maxlen=2000)

# The actual asyncio.Task each background worker is running (see main.py's lifespan) — lets the health page check
# real liveness (task.done()/cancelled()) instead of assuming a worker is fine just because nothing crashed the
# whole process.
WORKER_TASKS: dict[str, asyncio.Task] = {}

# When each worker last completed an iteration (success or handled failure — see record_worker_tick), and a short
# rolling history of tick outcomes for the "last N ticks" display.
WORKER_LAST_TICK: dict[str, datetime] = {}
WORKER_TICK_HISTORY: dict[str, deque] = {}
_TICK_HISTORY_LENGTH = 8


def record_worker_tick(name: str, status: str = "ok") -> None:
    """Called once per loop iteration by every worker (see workers/*.py) — real, not simulated: this only ever
    fires from inside an actual running loop, right after it does (or fails to do) its real work."""
    WORKER_LAST_TICK[name] = datetime.now(timezone.utc)
    history = WORKER_TICK_HISTORY.setdefault(name, deque(maxlen=_TICK_HISTORY_LENGTH))
    history.append(status)


def record_request(method: str, path: str, status_code: int, duration_ms: float) -> None:
    REQUEST_LOG.append({"method": method, "path": path, "status_code": status_code, "duration_ms": duration_ms, "at": datetime.now(timezone.utc)})


def record_query_duration(duration_ms: float) -> None:
    QUERY_DURATIONS.append(duration_ms)


def worker_status(name: str) -> tuple[Optional[str], list[str]]:
    """(human 'ran Xs/Xm ago' string or None if never ticked, tick history list oldest-first)."""
    last = WORKER_LAST_TICK.get(name)
    history = list(WORKER_TICK_HISTORY.get(name, []))
    if last is None:
        return None, history
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    if elapsed < 60:
        return f"ran {int(elapsed)}s ago", history
    if elapsed < 3600:
        return f"ran {int(elapsed // 60)}m ago", history
    return f"idle · {int(elapsed // 3600)}h ago", history
