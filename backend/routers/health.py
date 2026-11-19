"""Unauthenticated process-health endpoints only."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict:
    """Confirm that the API process can serve a request."""

    return {"status": "live"}


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    """Confirm only initialized functionality; do not invent dependency readiness."""

    runtime = request.app.state.runtime
    if not runtime.initialized:
        return _not_ready(request, "Application initialization has not completed.")
    if runtime.database_configured:
        try:
            database_available = await run_in_threadpool(
                request.app.state.database.check_connection
            )
        except Exception:
            database_available = False
        if not database_available:
            return _not_ready(request, "Database dependency is unavailable.")
    return JSONResponse(status_code=200, content={"status": "ready"})


def _not_ready(request: Request, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "status": "not_ready",
            "error": {
                "code": "not_ready",
                "message": message,
                "request_id": getattr(request.state, "request_id", "unavailable"),
            },
        },
    )
