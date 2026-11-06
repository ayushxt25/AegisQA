"""Unauthenticated process-health endpoints only."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


router = APIRouter(tags=["health"])


@router.get("/health/live")
async def liveness() -> dict:
    """Confirm that the API process can serve a request."""

    return {"status": "live"}


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    """Confirm only initialized functionality; do not invent dependency readiness."""

    runtime = request.app.state.runtime
    if runtime.initialized:
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(
        status_code=503,
        content={
            "status": "not_ready",
            "error": {
                "code": "not_ready",
                "message": "Application initialization has not completed.",
                "request_id": getattr(request.state, "request_id", "unavailable"),
            },
        },
    )
