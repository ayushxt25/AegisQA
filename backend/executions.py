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
from backend.models import ExecutionAttempt, ExecutionEvent, ExecutionJob, MembershipRole, ProjectMembership, User

_TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "recovery_required"}
_OPERATORS = {MembershipRole.ADMINISTRATOR.value, MembershipRole.OPERATOR.value}
_IDEMPOTENCY_CONSTRAINT = "uq_execution_jobs_project_idempotency"
_RETRY_PARENT_CONSTRAINT = "uq_execution_jobs_retry_parent"

class ExecutionService:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    def submit(self, actor: User, project_id: str, key: str, request: dict):
        fingerprint = _fingerprint(request)
        try:
            with self._database.session_scope() as session:
                self._role(session, actor, project_id, require_operator=True)
                job, attempt = self._new_job(session, actor, project_id, key, fingerprint, request, None)
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
                job, attempt = self._new_job(
                    session, actor, project_id, key, fingerprint, request, parent.id
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

    def _new_job(self, session, actor, project_id, key, fingerprint, request, retry_of):
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


def _deadline_expression(deadline_seconds: int):
    """Use PostgreSQL time with a typed bound duration, not application time."""

    seconds = bindparam(
        "deadline_seconds", value=deadline_seconds, type_=Integer()
    )
    return func.now() + seconds * text("INTERVAL '1 second'")


def _has_constraint(error: IntegrityError, constraint_name: str) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == constraint_name
