from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import AccessPilotError, access_pilot_error_handler, http_error_handler, unhandled_error_handler, validation_error_handler
from app.core.logging import configure_logging
from app.db.session import AsyncSessionLocal
from app.services.bootstrap import ensure_bootstrap_credential
from app.workers.activation import activation_worker_loop
from app.workers.expiration import expiration_worker_loop
from app.workers.scheduler import sync_scheduler_loop
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

configure_logging()
settings = get_settings()
logger = logging.getLogger("accesspilot.api")


class RequestIdMiddleware:
    """Plain ASGI middleware (not Starlette's BaseHTTPMiddleware) — BaseHTTPMiddleware has a well-known deadlock
    class where a client disconnecting mid-request (a closed tab, an aborted fetch) can wedge its internal
    send/receive bridge, freezing every subsequent request through it, including completely unrelated ones. Raw
    ASGI has no such bridge to wedge."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                request_id = value.decode()
                break
        request_id = request_id or str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


async def _log_bootstrap_credential_if_needed() -> None:
    """Only ever prints/does anything on a genuinely fresh install with no portal login IDP configured anywhere
    (env-var Entra or an active PortalAuthConfig) — a no-op, one-query check for every existing deployment,
    this one included."""
    async with AsyncSessionLocal() as session:
        password = await ensure_bootstrap_credential(session)
    if password:
        logger.warning("=" * 70)
        logger.warning("ACCESSPILOT FIRST-TIME SETUP REQUIRED — no portal login IDP is configured yet.")
        logger.warning("Bootstrap login — username: admin   password: %s", password)
        logger.warning("This password is shown ONLY ONCE. Use it to sign in and complete setup.")
        logger.warning("=" * 70)


@asynccontextmanager
async def lifespan(_: FastAPI):
    background_tasks: list[asyncio.Task] = []
    if settings.environment != "test":
        await _log_bootstrap_credential_if_needed()
        background_tasks.append(asyncio.create_task(sync_scheduler_loop(AsyncSessionLocal)))
        background_tasks.append(asyncio.create_task(expiration_worker_loop(AsyncSessionLocal)))
        background_tasks.append(asyncio.create_task(activation_worker_loop(AsyncSessionLocal)))
    yield
    for task in background_tasks:
        task.cancel()


app = FastAPI(title=settings.app_name, version="0.1.0", docs_url="/docs" if settings.environment != "production" else None, lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url], allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Content-Type", "X-Request-ID", "Authorization"])
app.add_exception_handler(AccessPilotError, access_pilot_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
app.include_router(v1_router)


@app.get("/health", tags=["health"])
async def root_health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
