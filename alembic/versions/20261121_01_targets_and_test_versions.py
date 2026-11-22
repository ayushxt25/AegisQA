"""Add approved browser targets and immutable test-definition versions.

Revision ID: 20261121_01
Revises: 20261120_02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261121_01"
down_revision = "20261120_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "browser_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "id", name="uq_browser_targets_project_id_id"),
        sa.UniqueConstraint("project_id", "name", name="uq_browser_targets_project_name"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_browser_targets_status"),
    )
    op.create_table(
        "browser_target_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("browser_target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(512), nullable=False),
        sa.Column("path_prefixes", postgresql.JSONB(), nullable=False),
        sa.Column("approval_state", sa.String(32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.ForeignKeyConstraint(["project_id", "browser_target_id"], ["browser_targets.project_id", "browser_targets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "id", name="uq_browser_target_versions_project_id_id"),
        sa.UniqueConstraint("browser_target_id", "version", name="uq_browser_target_versions_target_version"),
        sa.CheckConstraint("approval_state IN ('pending', 'approved', 'revoked')", name="ck_browser_target_versions_approval"),
        sa.CheckConstraint("jsonb_typeof(path_prefixes) = 'array'", name="ck_browser_target_versions_paths"),
    )
    op.create_index("ix_browser_target_versions_dispatch", "browser_target_versions", ["project_id", "approval_state"])
    op.create_table(
        "test_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "id", name="uq_test_definitions_project_id_id"),
        sa.UniqueConstraint("project_id", "name", name="uq_test_definitions_project_name"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_test_definitions_status"),
    )
    op.create_table(
        "test_definition_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("objective", sa.String(1000), nullable=False),
        sa.Column("action_policy", postgresql.JSONB(), nullable=False),
        sa.Column("assertion_contract", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "test_definition_id"], ["test_definitions.project_id", "test_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "id", name="uq_test_definition_versions_project_id_id"),
        sa.UniqueConstraint("test_definition_id", "version", name="uq_test_definition_versions_definition_version"),
        sa.CheckConstraint("jsonb_typeof(action_policy) = 'object'", name="ck_test_definition_versions_policy"),
        sa.CheckConstraint("jsonb_typeof(assertion_contract) = 'object'", name="ck_test_definition_versions_assertion"),
    )
    op.create_index("ix_test_definition_versions_project_created", "test_definition_versions", ["project_id", "created_at"])
    op.add_column("execution_jobs", sa.Column("browser_target_version_id", postgresql.UUID(as_uuid=True)))
    op.add_column("execution_jobs", sa.Column("test_definition_version_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key("fk_execution_jobs_target_version", "execution_jobs", "browser_target_versions", ["project_id", "browser_target_version_id"], ["project_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_execution_jobs_test_version", "execution_jobs", "test_definition_versions", ["project_id", "test_definition_version_id"], ["project_id", "id"], ondelete="RESTRICT")


def downgrade():
    op.drop_constraint("fk_execution_jobs_test_version", "execution_jobs", type_="foreignkey")
    op.drop_constraint("fk_execution_jobs_target_version", "execution_jobs", type_="foreignkey")
    op.drop_column("execution_jobs", "test_definition_version_id")
    op.drop_column("execution_jobs", "browser_target_version_id")
    op.drop_index("ix_test_definition_versions_project_created", table_name="test_definition_versions")
    op.drop_table("test_definition_versions")
    op.drop_table("test_definitions")
    op.drop_index("ix_browser_target_versions_dispatch", table_name="browser_target_versions")
    op.drop_table("browser_target_versions")
    op.drop_table("browser_targets")
