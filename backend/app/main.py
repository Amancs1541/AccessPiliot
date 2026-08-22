import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import AccessPilotError, access_pilot_error_handler, http_error_handler, unhandled_error_handler, validation_error_handler
from app.core.logging import configure_logging
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


app = FastAPI(title=settings.app_name, version="0.1.0", docs_url="/docs" if settings.environment != "production" else None)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url], allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Request-ID", "Authorization"])
app.add_exception_handler(AccessPilotError, access_pilot_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
app.include_router(v1_router)


@app.get("/health", tags=["health"])
async def root_health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
