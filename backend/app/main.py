from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import AccessPilotError, access_pilot_error_handler, http_error_handler, unhandled_error_handler, validation_error_handler
from app.core.logging import configure_logging
from app.db.session import AsyncSessionLocal
from app.workers.activation import activation_worker_loop
from app.workers.expiration import expiration_worker_loop
from app.workers.scheduler import sync_scheduler_loop
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

configure_logging()
settings = get_settings()
logger = logging.getLogger("accesspilot.api")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    background_tasks: list[asyncio.Task] = []
    if settings.environment != "test":
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
