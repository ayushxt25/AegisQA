"""Reconcile execution checks added after 20261120_01 was deployed.

Revision ID: 20261120_02
Revises: 20261120_01
"""
from alembic import op

revision = "20261120_02"
down_revision = "20261120_01"
branch_labels = None
depends_on = None

_CHECKS = (
    ("execution_jobs", "ck_execution_jobs_request_schema_version", "request_schema_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", "request_schema_version !~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'"),
    ("execution_jobs", "ck_execution_jobs_request_nonempty", "request_snapshot <> '{}'::jsonb", "request_snapshot = '{}'::jsonb"),
    ("execution_events", "ck_execution_events_identity_nonempty", "char_length(correlation_id) > 0 AND char_length(category) > 0", "char_length(correlation_id) = 0 OR char_length(category) = 0"),
)

def upgrade():
    for table, name, expression, invalid in _CHECKS:
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {table} WHERE {invalid}) THEN
                RAISE EXCEPTION 'cannot add {name}: existing data is invalid';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}' AND conrelid = '{table}'::regclass) THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression});
            END IF;
        END $$;""".format(table=table, name=name, expression=expression, invalid=invalid))

def downgrade():
    for table, name, _, __ in _CHECKS:
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}' AND conrelid = '{table}'::regclass) THEN
                ALTER TABLE {table} DROP CONSTRAINT {name};
            END IF;
        END $$;""".format(table=table, name=name))
