from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AccessPilotError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or str(uuid4())


def error_body(request: Request, code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "requestId": request_id(request), "details": details or {}}}


async def access_pilot_error_handler(request: Request, exc: AccessPilotError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_body(request, exc.code, exc.message, exc.details))


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    message = "The requested resource was not found." if exc.status_code == 404 else "The request could not be completed."
    return JSONResponse(status_code=exc.status_code, content=error_body(request, code, message))


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content=error_body(request, "VALIDATION_ERROR", "The request contains invalid data."))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=error_body(request, "INTERNAL_ERROR", "An internal error occurred."))
