"""FastAPI application factory for the Phase 1 backend foundation."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Callable, Optional, TextIO

from fastapi import FastAPI, Request, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.config import BackendSettings, load_settings
from backend.errors import register_exception_handlers
from backend.logging import configure_logging
from backend.routers.health import router as health_router


Initializer = Callable[[], None]


@dataclass
class RuntimeState:
    """In-memory state for this process only; no dependency readiness is implied."""

    initialized: bool = False
    initialization_error: Optional[str] = None
    shutdown_complete: bool = False


def create_app(
    settings: Optional[BackendSettings] = None,
    initializer: Optional[Initializer] = None,
    log_stream: Optional[TextIO] = None,
) -> FastAPI:
    """Create an isolated API instance without database or browser initialization."""

    resolved_settings = settings if settings is not None else load_settings()
    logger = configure_logging(
        resolved_settings,
        stream=log_stream,
        logger_name="aegisqa.api.{0}".format(uuid.uuid4().hex),
    )
    runtime = RuntimeState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            if initializer is not None:
                initializer()
            runtime.initialized = True
            logger.info(
                "application_started",
                extra={
                    "event": "application_started",
                    "environment": resolved_settings.environment,
                },
            )
        except Exception as error:
            runtime.initialized = False
            runtime.initialization_error = error.__class__.__name__
            logger.error(
                "application_initialization_failed",
                extra={
                    "event": "application_initialization_failed",
                    "error_type": error.__class__.__name__,
                },
            )
        try:
            yield
        finally:
            runtime.shutdown_complete = True
            logger.info("application_stopped", extra={"event": "application_stopped"})

    app = FastAPI(
        title="AegisQA API",
        version="0.1.0",
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.runtime = runtime
    app.state.logger = logger
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(resolved_settings.trusted_hosts))
    register_exception_handlers(app, logger)
    app.include_router(health_router)
    _register_request_logging(app, logger)
    return app


def _register_request_logging(app: FastAPI, logger) -> None:
    @app.middleware("http")
    async def request_correlation_and_logging(
        request: Request, call_next: Callable
    ) -> Response:
        request_id = _correlation_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        started_at = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration_ms = _duration_ms(started_at)
            logger.info(
                "request_completed",
                extra={
                    "event": "request_completed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": _route_template(request),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            raise

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "event": "request_completed",
                "request_id": request_id,
                "method": request.method,
                "route": _route_template(request),
                "status_code": status_code,
                "duration_ms": _duration_ms(started_at),
            },
        )
        return response


def _correlation_id(value: Optional[str]) -> str:
    if value:
        try:
            return str(uuid.UUID(value))
        except ValueError:
            pass
    return str(uuid.uuid4())


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


def _duration_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
