"""Add durable execution persistence foundation.

Revision ID: 20261120_01
Revises: 20261106_01
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261120_01"
down_revision = "20261106_01"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("execution_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_schema_version", sa.String(32), nullable=False), sa.Column("request_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False), sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)), sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False), sa.Column("retry_of_execution_job_id", postgresql.UUID(as_uuid=True)),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "retry_of_execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "id", name="uq_execution_jobs_project_id_id"), sa.UniqueConstraint("project_id", "idempotency_key", name="uq_execution_jobs_project_idempotency"), sa.UniqueConstraint("retry_of_execution_job_id", name="uq_execution_jobs_retry_parent"),
        sa.CheckConstraint("status IN ('queued','claimed','running','succeeded','failed','cancelled','timed_out','recovery_required')", name="ck_execution_jobs_status"), sa.CheckConstraint("jsonb_typeof(request_snapshot) = 'object'", name="ck_execution_jobs_request_object"))
    op.create_index("ix_execution_jobs_queue", "execution_jobs", ["status", "created_at"]); op.create_index("ix_execution_jobs_project_created", "execution_jobs", ["project_id", "created_at"])
    op.create_table("execution_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("execution_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default=sa.text("1")), sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("worker_id", sa.String(128)), sa.Column("lease_token", sa.String(128)), sa.Column("lease_expires_at", sa.DateTime(timezone=True)), sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("terminal_outcome", sa.String(64)),
        sa.ForeignKeyConstraint(["project_id", "execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"), sa.UniqueConstraint("execution_job_id", name="uq_execution_attempts_job"), sa.UniqueConstraint("project_id", "execution_job_id", "id", name="uq_execution_attempts_project_job_id"),
        sa.CheckConstraint("attempt_number = 1", name="ck_execution_attempts_one_per_job"), sa.CheckConstraint("status IN ('queued','claimed','starting','running','succeeded','failed','cancelled','timed_out','lost','finalization_failed')", name="ck_execution_attempts_status"))
    op.create_index("ix_execution_attempts_lease", "execution_attempts", ["status", "lease_expires_at"])
    op.create_table("execution_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("execution_job_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("execution_attempt_id", postgresql.UUID(as_uuid=True)),
        sa.Column("correlation_id", sa.String(64), nullable=False), sa.Column("category", sa.String(64), nullable=False), sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)), sa.Column("worker_id", sa.String(128)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["project_id", "execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["project_id", "execution_job_id", "execution_attempt_id"], ["execution_attempts.project_id", "execution_attempts.execution_job_id", "execution_attempts.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 8192", name="ck_execution_events_metadata"))
    op.create_index("ix_execution_events_history", "execution_events", ["project_id", "execution_job_id", "created_at", "id"])

def downgrade():
    op.drop_index("ix_execution_events_history", table_name="execution_events"); op.drop_table("execution_events")
    op.drop_index("ix_execution_attempts_lease", table_name="execution_attempts"); op.drop_table("execution_attempts")
    op.drop_index("ix_execution_jobs_project_created", table_name="execution_jobs"); op.drop_index("ix_execution_jobs_queue", table_name="execution_jobs"); op.drop_table("execution_jobs")
