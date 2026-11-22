"""Durable execution API service; workers are deliberately out of scope."""
from __future__ import annotations
import hashlib
import json
import uuid
from typing import Optional
from sqlalchemy import Integer, bindparam, func, select, text
from sqlalchemy.exc import IntegrityError
from backend.database import DatabaseManager
from backend.errors import SafeApiError
from backend.models import BrowserTargetVersion, ExecutionAttempt, ExecutionClassification, ExecutionEvent, ExecutionEvidence, ExecutionJob, MembershipRole, ProjectMembership, TestDefinitionVersion, User
from backend.targets import _url_in_target

_TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "recovery_required"}
_OPERATORS = {MembershipRole.ADMINISTRATOR.value, MembershipRole.OPERATOR.value}
_IDEMPOTENCY_CONSTRAINT = "uq_execution_jobs_project_idempotency"
_RETRY_PARENT_CONSTRAINT = "uq_execution_jobs_retry_parent"

class ExecutionService:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    def submit(self, actor: User, project_id: str, key: str, request: dict):
        request = _normalize_request(request)
        fingerprint = _fingerprint(request)
        try:
            with self._database.session_scope() as session:
                self._role(session, actor, project_id, require_operator=True)
                target_id, definition_id = self._binding(session, project_id, request)
                job, attempt = self._new_job(session, actor, project_id, key, fingerprint, request, None, target_id, definition_id)
                return job, attempt, False
        except IntegrityError as error:
            if not _has_constraint(error, _IDEMPOTENCY_CONSTRAINT):
                raise
            return self._idempotent(project_id, key, fingerprint)

    def list(self, actor: User, project_id: str, after: Optional[str], limit: int):
        with self._database.session_scope() as session:
            self._role(session, actor, project_id, require_operator=False)
            query = (
                select(ExecutionJob)
                .where(ExecutionJob.project_id == project_id)
                .order_by(ExecutionJob.id)
                .limit(limit + 1)
            )
            if after:
                query = query.where(ExecutionJob.id > after)
            rows = list(session.scalars(query))
            return rows[:limit], (str(rows[limit].id) if len(rows) > limit else None)

    def detail(self, actor: User, project_id: str, job_id: str):
        with self._database.session_scope() as session:
            self._role(session, actor, project_id, require_operator=False)
            job = session.scalar(select(ExecutionJob).where(ExecutionJob.project_id == project_id, ExecutionJob.id == job_id))
            if job is None: raise SafeApiError(404, "not_found", "Resource not found.")
            attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.project_id == project_id, ExecutionAttempt.execution_job_id == job.id))
            events = list(session.scalars(select(ExecutionEvent).where(ExecutionEvent.project_id == project_id, ExecutionEvent.execution_job_id == job.id).order_by(ExecutionEvent.created_at, ExecutionEvent.id)))
            return job, attempt, events

    def evidence(self, actor: User, project_id: str, job_id: str):
        with self._database.session_scope() as session:
            self._role(session, actor, project_id, require_operator=False)
            job = session.scalar(select(ExecutionJob.id).where(
                ExecutionJob.project_id == project_id, ExecutionJob.id == job_id,
            ))
            if job is None:
                raise SafeApiError(404, "not_found", "Resource not found.")
            rows = session.execute(
                select(ExecutionEvidence).where(
                    ExecutionEvidence.project_id == project_id,
                    ExecutionEvidence.execution_job_id == job_id,
                ).order_by(ExecutionEvidence.captured_at, ExecutionEvidence.id)
            ).scalars().all()
            return rows

    def classification(self, actor: User, project_id: str, job_id: str):
        with self._database.session_scope() as session:
            self._role(session, actor, project_id, require_operator=False)
            job = session.scalar(select(ExecutionJob.id).where(
                ExecutionJob.project_id == project_id, ExecutionJob.id == job_id,
            ))
            if job is None:
                raise SafeApiError(404, "not_found", "Resource not found.")
            return session.scalar(select(ExecutionClassification).where(
                ExecutionClassification.project_id == project_id,
                ExecutionClassification.execution_job_id == job_id,
            ))

    def cancel(self, actor: User, project_id: str, job_id: str):
        with self._database.session_scope() as session:
            self._role(session, actor, project_id, require_operator=True)
            job = session.scalar(select(ExecutionJob).where(ExecutionJob.project_id == project_id, ExecutionJob.id == job_id).with_for_update())
            if job is None: raise SafeApiError(404, "not_found", "Resource not found.")
            attempt = session.scalar(select(ExecutionAttempt).where(ExecutionAttempt.project_id == project_id, ExecutionAttempt.execution_job_id == job.id).with_for_update())
            if job.status in _TERMINAL: return job
            if job.status == "queued":
                job.status = "cancelled"
                job.updated_at = func.now()
                attempt.status = "cancelled"
                attempt.finished_at = func.now()
                category = "execution_cancelled"
            elif job.cancel_requested_at is None:
                job.cancel_requested_at = func.now()
                job.updated_at = func.now()
                category = "cancel_requested"
            else:
                return job
            self._event(session, project_id, job.id, attempt.id, actor.id, category)
            return job

    def retry(self, actor: User, project_id: str, job_id: str, key: str):
        try:
            with self._database.session_scope() as session:
                self._role(session, actor, project_id, require_operator=True)
                parent = session.scalar(select(ExecutionJob).where(ExecutionJob.project_id == project_id, ExecutionJob.id == job_id).with_for_update())
                if parent is None: raise SafeApiError(404, "not_found", "Resource not found.")
                if parent.status not in _TERMINAL: raise SafeApiError(409, "execution_not_retryable", "Execution is not retryable.")
                request = parent.request_snapshot
                fingerprint = _fingerprint(request)
                target_id, definition_id = self._retry_binding(session, project_id, parent)
                job, attempt = self._new_job(
                    session, actor, project_id, key, fingerprint, request, parent.id, target_id, definition_id
                )
                self._event(session, project_id, parent.id, None, actor.id, "execution_retry_requested")
                return job, attempt, False
        except IntegrityError as error:
            if _has_constraint(error, _IDEMPOTENCY_CONSTRAINT):
                return self._retry_idempotent(project_id, job_id, key)
            if not _has_constraint(error, _RETRY_PARENT_CONSTRAINT):
                raise
            with self._database.session_scope() as session:
                child = session.scalar(
                    select(ExecutionJob).where(
                        ExecutionJob.project_id == project_id,
                        ExecutionJob.retry_of_execution_job_id == job_id,
                    )
                )
                if child is not None and child.idempotency_key == key:
                    return self._attempt_for_job(session, project_id, child, replayed=True)
            raise SafeApiError(409, "retry_already_exists", "Retry already exists.") from None

    def _new_job(self, session, actor, project_id, key, fingerprint, request, retry_of, target_version_id=None, definition_version_id=None):
        deadline = session.scalar(select(_deadline_expression(request["deadline_seconds"])))
        job = ExecutionJob(
            id=uuid.uuid4(),
            project_id=project_id,
            requested_by_user_id=actor.id,
            request_schema_version=request["schema_version"],
            request_snapshot=request,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            status="queued",
            deadline_at=deadline,
            retry_of_execution_job_id=retry_of,
            browser_target_version_id=target_version_id,
            test_definition_version_id=definition_version_id,
        )
        attempt = ExecutionAttempt(
            id=uuid.uuid4(),
            project_id=project_id,
            execution_job_id=job.id,
            attempt_number=1,
            status="queued",
        )
        session.add(job)
        # Models deliberately have no ORM relationships; flush the composite-FK
        # parent before the child rather than relying on unit-of-work ordering.
        session.flush()
        session.add(attempt)
        session.flush()
        self._event(session, project_id, job.id, attempt.id, actor.id, "execution_submitted")
        session.flush()
        return job, attempt

    def _binding(self, session, project_id, request):
        target_id = request.get("browser_target_version_id")
        definition_id = request.get("test_definition_version_id")
        if target_id is None and definition_id is None:
            # Direct P1.4 service callers remain supported; public API requires both.
            return None, None
        if target_id is None or definition_id is None:
            raise SafeApiError(422, "validation_error", "Request validation failed.")
        target = session.scalar(select(BrowserTargetVersion).where(BrowserTargetVersion.project_id == project_id, BrowserTargetVersion.id == target_id, BrowserTargetVersion.approval_state == "approved"))
        definition = session.scalar(select(TestDefinitionVersion).where(TestDefinitionVersion.project_id == project_id, TestDefinitionVersion.id == definition_id))
        if target is None or definition is None:
            raise SafeApiError(409, "execution_contract_unavailable", "Execution contract is unavailable.")
        if not all(_url_in_target(url, target.origin, target.path_prefixes) for url in definition.action_policy.get("allowed_urls", [])):
            raise SafeApiError(422, "validation_error", "Request validation failed.")
        return target.id, definition.id

    def _retry_binding(self, session, project_id, parent):
        if parent.browser_target_version_id is None and parent.test_definition_version_id is None:
            return None, None
        return self._binding(session, project_id, {"browser_target_version_id": parent.browser_target_version_id, "test_definition_version_id": parent.test_definition_version_id})

    def _idempotent(self, project_id, key, fingerprint):
        with self._database.session_scope() as session:
            job = session.scalar(select(ExecutionJob).where(ExecutionJob.project_id == project_id, ExecutionJob.idempotency_key == key))
            if job is None or job.request_fingerprint != fingerprint:
                raise SafeApiError(409, "idempotency_conflict", "Idempotency key conflicts.")
            return self._attempt_for_job(session, project_id, job, replayed=True)

    def _retry_idempotent(self, project_id, parent_id, key):
        with self._database.session_scope() as session:
            job = session.scalar(
                select(ExecutionJob).where(
                    ExecutionJob.project_id == project_id,
                    ExecutionJob.idempotency_key == key,
                )
            )
            if job is None or str(job.retry_of_execution_job_id) != parent_id:
                raise SafeApiError(409, "idempotency_conflict", "Idempotency key conflicts.")
            return self._attempt_for_job(session, project_id, job, replayed=True)

    def _attempt_for_job(self, session, project_id, job, replayed):
        attempt = session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.project_id == project_id,
                ExecutionAttempt.execution_job_id == job.id,
            )
        )
        return job, attempt, replayed

    def _role(self, session, actor, project_id, require_operator):
        membership = session.scalar(
            select(ProjectMembership).where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == actor.id,
                ProjectMembership.revoked_at.is_(None),
            )
        )
        if membership is None:
            raise SafeApiError(404, "not_found", "Resource not found.")
        if require_operator and membership.role not in _OPERATORS:
            raise SafeApiError(403, "forbidden", "Permission denied.")

    def _event(self, session, project_id, job_id, attempt_id, actor_id, category):
        session.add(
            ExecutionEvent(
                id=uuid.uuid4(),
                project_id=project_id,
                execution_job_id=job_id,
                execution_attempt_id=attempt_id,
                correlation_id=str(uuid.uuid4()),
                category=category,
                event_metadata={},
                actor_user_id=actor_id,
            )
        )


def _fingerprint(request):
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _normalize_request(request):
    if not isinstance(request, dict):
        raise SafeApiError(422, "validation_error", "Request validation failed.")
    normalized = dict(request)
    for field in ("browser_target_version_id", "test_definition_version_id"):
        if isinstance(normalized.get(field), uuid.UUID):
            normalized[field] = str(normalized[field])
    try:
        return json.loads(json.dumps(normalized, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError):
        raise SafeApiError(422, "validation_error", "Request validation failed.") from None


def _deadline_expression(deadline_seconds: int):
    """Use PostgreSQL time with a typed bound duration, not application time."""

    seconds = bindparam(
        "deadline_seconds", value=deadline_seconds, type_=Integer()
    )
    return func.now() + seconds * text("INTERVAL '1 second'")


def _has_constraint(error: IntegrityError, constraint_name: str) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == constraint_name
