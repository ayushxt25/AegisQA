import base64
import json
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field, model_validator
from backend.auth import get_current_user
from backend.errors import SafeApiError
from backend.models import User

router = APIRouter(prefix="/v1", tags=["executions"])


class ExecutionRequest(BaseModel):
    schema_version: str = Field(pattern=r"^1\.0\.0$")
    deadline_seconds: int = Field(ge=1, le=3600)
    orchestration: dict = Field(default_factory=dict)
    browser_target_version_id: UUID
    test_definition_version_id: UUID

    @model_validator(mode="after")
    def validate_orchestration_size(self):
        try:
            encoded = json.dumps(
                self.orchestration, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("orchestration must be JSON-compatible") from error
        if len(encoded) > 4096:
            raise ValueError("orchestration is too large")
        return self


class ExecutionOut(BaseModel):
    id: str
    status: str
    created_at: object
    deadline_at: object
    cancel_requested_at: Optional[object]


def _svc(request):
    return request.app.state.execution_service


def _out(job):
    return ExecutionOut(
        id=str(job.id),
        status=job.status,
        created_at=job.created_at,
        deadline_at=job.deadline_at,
        cancel_requested_at=job.cancel_requested_at,
    )
def _cursor(value):
    if value is None: return None
    try: return str(UUID(base64.urlsafe_b64decode(value + "==").decode()))
    except Exception: raise SafeApiError(422, "validation_error", "Request validation failed.") from None
def _encode(value): return None if value is None else base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _idempotency_key(value: str) -> str:
    if not value or not value.strip():
        raise SafeApiError(422, "validation_error", "Request validation failed.")
    return value


@router.post("/projects/{project_id}/executions", status_code=202)
def submit(project_id: UUID, body: ExecutionRequest, request: Request, idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128), user: User = Depends(get_current_user)):
    job, attempt, replay = _svc(request).submit(user, str(project_id), _idempotency_key(idempotency_key), body.model_dump(mode="json"))
    return {"job": _out(job).model_dump(), "attempt_id": str(attempt.id), "replayed": replay}
@router.get("/projects/{project_id}/executions")
def list_execution(project_id: UUID, request: Request, cursor: Optional[str] = None, limit: int = Query(20, ge=1, le=100), user: User = Depends(get_current_user)):
    rows, next_cursor = _svc(request).list(user, str(project_id), _cursor(cursor), limit); return {"items": [_out(row).model_dump() for row in rows], "next_cursor": _encode(next_cursor)}
@router.get("/projects/{project_id}/executions/{job_id}")
def detail(project_id: UUID, job_id: UUID, request: Request, user: User = Depends(get_current_user)):
    job, attempt, events = _svc(request).detail(user, str(project_id), str(job_id))
    evidence = _svc(request).evidence(user, str(project_id), str(job_id))
    classification = _svc(request).classification(user, str(project_id), str(job_id))
    return {
        "job": _out(job).model_dump(),
        "attempt": None if attempt is None else {"id": str(attempt.id), "status": attempt.status},
        "events": [
            {"id": str(event.id), "category": event.category, "created_at": event.created_at}
            for event in events
        ],
        "evidence": [
            {"evidence_id": item.evidence_id, "source": item.source,
             "producing_component": item.producing_component, "captured_at": item.captured_at,
             "content_digest": item.content_digest, "observed_facts": item.observed_facts}
            for item in evidence
        ],
        "classification": None if classification is None else {
            "classifier_version": classification.classifier_version,
            "category": classification.category, "confidence": classification.confidence,
            "applied_rule_id": classification.applied_rule_id,
            "evidence_references": classification.evidence_references,
            "missing_evidence": classification.missing_evidence,
            "created_at": classification.created_at,
        },
    }
@router.post("/projects/{project_id}/executions/{job_id}/cancel", status_code=202)
def cancel(project_id: UUID, job_id: UUID, request: Request, user: User = Depends(get_current_user)): return _out(_svc(request).cancel(user, str(project_id), str(job_id))).model_dump()
@router.post("/projects/{project_id}/executions/{job_id}/retry", status_code=202)
def retry(project_id: UUID, job_id: UUID, request: Request, idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128), user: User = Depends(get_current_user)):
    job, attempt, replay = _svc(request).retry(user, str(project_id), str(job_id), _idempotency_key(idempotency_key))
    return {"job": _out(job).model_dump(), "attempt_id": str(attempt.id), "replayed": replay}
