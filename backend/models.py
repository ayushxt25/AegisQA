"""Initial PostgreSQL identity and project persistence schema."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative metadata used exclusively by Alembic migrations."""


class MembershipRole(str, enum.Enum):
    ADMINISTRATOR = "administrator"
    OPERATOR = "operator"
    REVIEWER = "reviewer"


class ExecutionJobStatus(str, enum.Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    RECOVERY_REQUIRED = "recovery_required"


class ExecutionAttemptStatus(str, enum.Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    LOST = "lost"
    FINALIZATION_FAILED = "finalization_failed"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        UniqueConstraint("identity_subject", name="uq_users_identity_subject"),
        Index("ix_users_email", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    last_authenticated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status"),
        UniqueConstraint("slug", name="uq_projects_slug"),
        Index("ix_projects_created_by_user_id", "created_by_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('administrator', 'operator', 'reviewer')",
            name="ck_project_memberships_role",
        ),
        Index(
            "uq_active_project_membership",
            "project_id",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionJob(Base):
    __tablename__ = "execution_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_execution_jobs_project_id_id"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_execution_jobs_project_idempotency"),
        UniqueConstraint("retry_of_execution_job_id", name="uq_execution_jobs_retry_parent"),
        CheckConstraint("status IN ('queued','claimed','running','succeeded','failed','cancelled','timed_out','recovery_required')", name="ck_execution_jobs_status"),
        CheckConstraint("jsonb_typeof(request_snapshot) = 'object'", name="ck_execution_jobs_request_object"),
        CheckConstraint("request_schema_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name="ck_execution_jobs_request_schema_version"),
        CheckConstraint("request_snapshot <> '{}'::jsonb", name="ck_execution_jobs_request_nonempty"),
        ForeignKeyConstraint(["project_id", "retry_of_execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"),
        Index("ix_execution_jobs_queue", "status", "created_at"),
        Index("ix_execution_jobs_project_created", "project_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    request_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    request_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'queued'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    cancel_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retry_of_execution_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"
    __table_args__ = (
        UniqueConstraint("execution_job_id", name="uq_execution_attempts_job"),
        UniqueConstraint("project_id", "execution_job_id", "id", name="uq_execution_attempts_project_job_id"),
        CheckConstraint("attempt_number = 1", name="ck_execution_attempts_one_per_job"),
        CheckConstraint("status IN ('queued','claimed','starting','running','succeeded','failed','cancelled','timed_out','lost','finalization_failed')", name="ck_execution_attempts_status"),
        ForeignKeyConstraint(["project_id", "execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"),
        Index("ix_execution_attempts_lease", "status", "lease_expires_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'queued'"))
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_outcome: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id", "execution_job_id", "execution_attempt_id"], ["execution_attempts.project_id", "execution_attempts.execution_job_id", "execution_attempts.id"], ondelete="RESTRICT"),
        CheckConstraint("jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 8192", name="ck_execution_events_metadata"),
        CheckConstraint("char_length(correlation_id) > 0 AND char_length(category) > 0", name="ck_execution_events_identity_nonempty"),
        Index("ix_execution_events_history", "project_id", "execution_job_id", "created_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_attempt_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
