"""Safe public error responses for the backend foundation."""

from __future__ import annotations

import logging
from typing import Dict, Tuple

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


_HTTP_ERRORS: Dict[int, Tuple[str, str]] = {
    404: ("not_found", "Resource not found."),
    405: ("method_not_allowed", "Method not allowed."),
}


def register_exception_handlers(app: FastAPI, logger: logging.Logger) -> None:
    """Register JSON-only handlers that do not echo request input."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del exc
        return _error_response(request, 422, "validation_error", "Request validation failed.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _HTTP_ERRORS.get(
            exc.status_code, ("request_error", "Request could not be completed.")
        )
        return _error_response(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unexpected_request_error",
            extra={
                "event": "unexpected_request_error",
                "request_id": _request_id(request),
                "error_type": exc.__class__.__name__,
            },
        )
        return _error_response(
            request, 500, "internal_error", "An internal error occurred."
        )


def _error_response(
    request: Request, status_code: int, code: str, message: str
) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=status_code,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unavailable")
