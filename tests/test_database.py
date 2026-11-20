"""PostgreSQL-only tests for the P1.2 persistence foundation."""

from __future__ import annotations

import os
import re
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional, Tuple
from unittest.mock import MagicMock, Mock, patch

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app import create_app
from backend.auth import AuthenticatedPrincipal, DisabledAccountError, UserProvisioner
from backend.config import BackendSettings, ConfigurationError, load_settings
from backend.database import DatabaseManager, redact_database_url
from backend.errors import SafeApiError
from backend.executions import ExecutionService, _deadline_expression, _has_constraint
from backend.models import ExecutionAttempt, ExecutionEvent, ExecutionJob, MembershipRole, Project, ProjectMembership, User
from backend.projects import ProjectService
from backend.worker import ClaimedAttempt, ExecutionWorker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POSTGRES_DEFAULT_PORT = 5432
_OWNED_SCHEMA_PATTERN = re.compile(r"^aegisqa_it_[a-f0-9]{32}$")
_POSTGRES_TEMP_SCHEMA_PATTERN = re.compile(r"^pg_(?:toast_)?temp_[0-9]+$")
_POSTGRES_SYSTEM_SCHEMAS = frozenset(("pg_catalog", "information_schema", "public", "pg_toast"))


@dataclass(frozen=True)
class IsolatedTestTarget:
    """Verified disposable database plus a schema owned by one test class."""

    database_url: str
    schema: str


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


def _prepare_isolated_test_target() -> IsolatedTestTarget:
    """Fail closed before creating a schema used for destructive integration work."""

    if os.environ.get("AEGISQA_RUN_POSTGRES_INTEGRATION_TESTS") != "true":
        raise unittest.SkipTest("Set AEGISQA_RUN_POSTGRES_INTEGRATION_TESTS=true to opt in")
    test_database_url = os.environ.get("AEGISQA_TEST_DATABASE_URL")
    development_url = os.environ.get("AEGISQA_DATABASE_URL")
    if not test_database_url:
        raise unittest.SkipTest("AEGISQA_TEST_DATABASE_URL is not configured")
    if not development_url:
        raise RuntimeError("AEGISQA_DATABASE_URL must identify a separate development target.")
    try:
        parsed = make_url(test_database_url)
        development = make_url(development_url)
    except Exception:
        raise RuntimeError("Database target configuration is invalid.") from None
    _validate_postgresql_target(parsed, "test")
    _validate_postgresql_target(development, "development")
    if _same_database_target(parsed, development):
        raise RuntimeError("Test and development database targets must be distinct.")

    test_identity = _read_database_identity(test_database_url)
    development_identity = _read_database_identity(development_url)
    if test_identity[0] != str(parsed.database):
        raise RuntimeError("Connected test database identity does not match configuration.")
    if development_identity[0] != str(development.database):
        raise RuntimeError("Connected development database identity does not match configuration.")
    if test_identity == development_identity:
        raise RuntimeError("Test and development database identities must be distinct.")

    engine = create_engine(test_database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            _assert_database_is_disposable(
                _read_non_system_schemas(connection),
                _read_user_tables(connection),
            )
            schema = "aegisqa_it_{0}".format(uuid.uuid4().hex)
            connection.execute(text('CREATE SCHEMA "{0}"'.format(schema)))
            connection.commit()
    except Exception:
        raise RuntimeError("Unable to establish an isolated disposable PostgreSQL test target.") from None
    finally:
        engine.dispose()
    return IsolatedTestTarget(database_url=test_database_url, schema=schema)


def _validate_postgresql_target(url, label: str) -> None:
    if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
        raise RuntimeError("{0} database target is not a complete PostgreSQL psycopg URL.".format(label))


def _read_database_identity(database_url: str) -> Tuple[str, str, int]:
    """Read a non-secret server/database identity using a bounded connection."""

    engine = None
    try:
        engine = create_engine(database_url, connect_args={"connect_timeout": 3})
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT current_database(), "
                    "COALESCE(inet_server_addr()::text, ''), inet_server_port()"
                )
            ).one()
        return (str(row[0]), _canonical_host(str(row[1])), int(row[2]))
    except Exception:
        raise RuntimeError("Unable to establish database target identity.") from None
    finally:
        if engine is not None:
            engine.dispose()


def _same_database_target(first, second) -> bool:
    """Compare connection targets without putting credentials in failures."""

    return (
        first.drivername == second.drivername
        and _canonical_host(str(first.host)) == _canonical_host(str(second.host))
        and (first.port or _POSTGRES_DEFAULT_PORT) == (second.port or _POSTGRES_DEFAULT_PORT)
        and first.database == second.database
    )


def _canonical_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return "loopback"
    return normalized


def _read_non_system_schemas(connection) -> Tuple[str, ...]:
    rows = connection.execute(
        text(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'pg_toast') "
            "AND schema_name !~ '^pg_temp_[0-9]+$' "
            "AND schema_name !~ '^pg_toast_temp_[0-9]+$' "
            "ORDER BY schema_name"
        )
    )
    return tuple(str(row[0]) for row in rows)


def _read_user_tables(connection) -> Tuple[Tuple[str, str], ...]:
    rows = connection.execute(
        text(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' "
            "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY table_schema, table_name"
        )
    )
    return tuple((str(row[0]), str(row[1])) for row in rows)


def _assert_database_is_disposable(
    non_system_schemas: Iterable[str], user_tables: Iterable[Tuple[str, str]]
) -> None:
    """Require a freshly provisioned database; a suffix never proves ownership."""

    application_schemas = tuple(
        schema for schema in non_system_schemas if not _is_postgresql_system_schema(schema)
    )
    if application_schemas or tuple(user_tables):
        raise RuntimeError("Test database is not empty and cannot be treated as disposable.")


def _is_postgresql_system_schema(schema: str) -> bool:
    return schema in _POSTGRES_SYSTEM_SCHEMAS or _POSTGRES_TEMP_SCHEMA_PATTERN.fullmatch(schema) is not None


def _create_isolated_engine(target: IsolatedTestTarget):
    return create_engine(
        target.database_url,
        connect_args={"options": "-csearch_path={0}".format(target.schema)},
    )


def _activate_isolated_target(target: IsolatedTestTarget) -> Tuple[Optional[str], Optional[str]]:
    previous_database_url = os.environ.get("AEGISQA_DATABASE_URL")
    previous_schema = os.environ.get("AEGISQA_ALEMBIC_SCHEMA")
    os.environ["AEGISQA_DATABASE_URL"] = target.database_url
    os.environ["AEGISQA_ALEMBIC_SCHEMA"] = target.schema
    return previous_database_url, previous_schema


def _drop_isolated_schema(target: IsolatedTestTarget) -> None:
    """Drop only the unique schema created by this run, including failed test state."""

    if not _OWNED_SCHEMA_PATTERN.fullmatch(target.schema):
        raise RuntimeError("Refusing to drop a schema not owned by this test run.")
    engine = create_engine(target.database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS "{0}" CASCADE'.format(target.schema)))
    finally:
        engine.dispose()


def _restore_database_url(value: Optional[str]) -> None:
    if value is None:
        os.environ.pop("AEGISQA_DATABASE_URL", None)
    else:
        os.environ["AEGISQA_DATABASE_URL"] = value


def _restore_alembic_schema(value: Optional[str]) -> None:
    if value is None:
        os.environ.pop("AEGISQA_ALEMBIC_SCHEMA", None)
    else:
        os.environ["AEGISQA_ALEMBIC_SCHEMA"] = value


def _run_isolated_migration_cycle(target: IsolatedTestTarget, verify_upgraded, verify_downgraded) -> None:
    """Run migration verification and always remove the run-owned schema."""

    original_database_url, original_schema = _activate_isolated_target(target)
    engine = None
    config = _alembic_config()
    try:
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        engine = _create_isolated_engine(target)
        verify_upgraded(engine)
        command.downgrade(config, "base")
        verify_downgraded(engine)
    finally:
        if engine is not None:
            engine.dispose()
        try:
            _drop_isolated_schema(target)
        finally:
            _restore_database_url(original_database_url)
            _restore_alembic_schema(original_schema)


class DatabaseConfigurationTest(unittest.TestCase):
    def test_database_url_validation_and_redaction(self) -> None:
        secret = "not-for-logs"
        url = "postgresql+psycopg://user:{0}@127.0.0.1:5432/aegisqa_test".format(secret)
        settings = BackendSettings(environment="test", database_url=url)
        self.assertEqual(url, settings.database_url)
        self.assertNotIn(secret, redact_database_url(url))
        with self.assertRaises(ValidationError):
            BackendSettings(environment="test", database_url="sqlite:///local.db")
        with self.assertRaises(ValidationError):
            BackendSettings(environment="production")
        with self.assertRaises(ConfigurationError) as error:
            load_settings({"AEGISQA_DATABASE_URL": "sqlite:///secret-value"})
        self.assertNotIn("secret-value", str(error.exception))

    def test_execution_deadline_is_parameterized_postgresql_time_at_bounds(self) -> None:
        for seconds in (1, 3600):
            compiled = (
                _deadline_expression(seconds).compile(dialect=postgresql.dialect())
            )
            self.assertIn("now()", str(compiled))
            self.assertIn("deadline_seconds", str(compiled))
            self.assertIn("INTERVAL '1 second'", str(compiled))
            self.assertEqual(seconds, compiled.params["deadline_seconds"])

    def test_execution_integrity_matching_requires_the_named_constraint(self) -> None:
        class Diagnostic:
            constraint_name = "uq_execution_jobs_project_idempotency"

        failure = Exception("unique violation")
        failure.diag = Diagnostic()
        matching = IntegrityError("statement", {}, failure)
        unrelated = IntegrityError("statement", {}, Exception("foreign key"))
        self.assertTrue(_has_constraint(matching, "uq_execution_jobs_project_idempotency"))
        self.assertFalse(_has_constraint(unrelated, "uq_execution_jobs_project_idempotency"))

    def test_execution_service_propagates_unrelated_integrity_error(self) -> None:
        database = Mock()
        scope = MagicMock()
        scope.__enter__.return_value = object()
        database.session_scope.return_value = scope
        service = ExecutionService(database)
        unrelated = IntegrityError("statement", {}, Exception("foreign key"))
        with patch.object(service, "_role"), patch.object(
            service, "_new_job", side_effect=unrelated
        ):
            with self.assertRaises(IntegrityError):
                service.submit(
                    Mock(),
                    "project-id",
                    "key",
                    {"schema_version": "1.0.0", "deadline_seconds": 1},
                )

    def test_worker_configuration_bounds_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            BackendSettings(environment="test", execution_worker_lease_seconds=4)
        with self.assertRaises(ValidationError):
            BackendSettings(
                environment="test",
                execution_worker_lease_seconds=10,
                execution_worker_heartbeat_seconds=10,
            )

    def test_engine_is_lazy_and_session_scope_requires_configuration(self) -> None:
        settings = BackendSettings(
            environment="test",
            database_url="postgresql+psycopg://user:password@127.0.0.1:1/aegisqa_test",
            database_connect_timeout_seconds=1,
        )
        database = DatabaseManager(settings)
        self.assertIsNone(database.engine)
        database.start()
        self.assertIsNotNone(database.engine)
        database.dispose()
        self.assertIsNone(database.engine)
        with self.assertRaises(RuntimeError):
            with DatabaseManager(BackendSettings(environment="test")).session_scope():
                pass

    def test_database_failure_does_not_make_liveness_fail(self) -> None:
        secret = "database-secret"
        settings = BackendSettings(
            environment="test",
            database_url="postgresql+psycopg://user:{0}@127.0.0.1:1/aegisqa_test".format(secret),
            database_connect_timeout_seconds=1,
        )
        app = create_app(settings=settings)
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            self.assertEqual(200, client.get("/health/live").status_code)
            ready = client.get("/health/ready")
        self.assertEqual(503, ready.status_code)
        self.assertEqual("not_ready", ready.json()["error"]["code"])
        self.assertNotIn(secret, ready.text)

    def test_readiness_rechecks_database_and_recovers(self) -> None:
        app = create_app(settings=BackendSettings(environment="test"))
        app.state.runtime.database_configured = True
        app.state.database.check_connection = Mock(side_effect=(False, True))
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            self.assertEqual(200, client.get("/health/live").status_code)
            self.assertEqual(503, client.get("/health/ready").status_code)
            self.assertEqual(200, client.get("/health/ready").status_code)
        self.assertEqual(2, app.state.database.check_connection.call_count)

    def test_database_target_comparison_normalizes_loopback_and_default_port(self) -> None:
        localhost = make_url("postgresql+psycopg://user:password@localhost/aegisqa")
        loopback = make_url("postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa")
        other_database = make_url("postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa_test")
        self.assertTrue(_same_database_target(localhost, loopback))
        self.assertFalse(_same_database_target(localhost, other_database))

    def test_incomplete_or_wrong_driver_targets_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL psycopg"):
            _validate_postgresql_target(make_url("postgresql://user:password@localhost/aegisqa"), "test")
        with self.assertRaisesRegex(RuntimeError, "complete"):
            _validate_postgresql_target(make_url("postgresql+psycopg:///aegisqa"), "test")

    def test_nonempty_targets_are_rejected_as_not_disposable(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not empty"):
            _assert_database_is_disposable(("other_app",), ())
        with self.assertRaisesRegex(RuntimeError, "not empty"):
            _assert_database_is_disposable((), (("public", "users"),))
        _assert_database_is_disposable((), ())

    def test_internal_postgresql_schemas_do_not_make_a_clean_target_non_disposable(self) -> None:
        _assert_database_is_disposable(
            ("pg_toast", "pg_temp_3", "pg_toast_temp_3"),
            (),
        )

    def test_isolated_target_environment_is_restored_after_migration_failure(self) -> None:
        target = IsolatedTestTarget(
            database_url="postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa_test",
            schema="aegisqa_it_" + "a" * 32,
        )
        with patch.dict(
            os.environ,
            {"AEGISQA_DATABASE_URL": "postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa"},
            clear=True,
        ):
            original_database_url, original_schema = _activate_isolated_target(target)
            try:
                self.assertEqual(target.database_url, os.environ["AEGISQA_DATABASE_URL"])
                self.assertEqual(target.schema, os.environ["AEGISQA_ALEMBIC_SCHEMA"])
                raise RuntimeError("simulated migration failure")
            except RuntimeError:
                pass
            finally:
                _restore_database_url(original_database_url)
                _restore_alembic_schema(original_schema)
            self.assertEqual(
                "postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa",
                os.environ["AEGISQA_DATABASE_URL"],
            )
            self.assertNotIn("AEGISQA_ALEMBIC_SCHEMA", os.environ)

    def test_schema_cleanup_targets_only_the_owned_schema(self) -> None:
        target = IsolatedTestTarget(
            database_url="postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa_test",
            schema="aegisqa_it_" + "b" * 32,
        )
        engine = MagicMock()
        connection = Mock()
        engine.begin.return_value.__enter__.return_value = connection
        with patch(__name__ + ".create_engine", return_value=engine):
            _drop_isolated_schema(target)
        statement = str(connection.execute.call_args[0][0])
        self.assertIn('DROP SCHEMA IF EXISTS "{0}" CASCADE'.format(target.schema), statement)
        self.assertNotIn("public", statement)
        engine.dispose.assert_called_once()

    def test_schema_cleanup_refuses_an_unowned_schema(self) -> None:
        target = IsolatedTestTarget(
            database_url="postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa_test",
            schema="public",
        )
        with patch(__name__ + ".create_engine") as create_engine_mock:
            with self.assertRaisesRegex(RuntimeError, "not owned"):
                _drop_isolated_schema(target)
        create_engine_mock.assert_not_called()

    def test_migration_failure_still_drops_schema_and_restores_environment(self) -> None:
        target = IsolatedTestTarget(
            database_url="postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa_test",
            schema="aegisqa_it_" + "c" * 32,
        )
        with patch.dict(
            os.environ,
            {"AEGISQA_DATABASE_URL": "postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa"},
            clear=True,
        ), patch(__name__ + ".command.downgrade", side_effect=RuntimeError("migration failed")), patch(
            __name__ + "._drop_isolated_schema"
        ) as drop_schema:
            with self.assertRaisesRegex(RuntimeError, "migration failed"):
                _run_isolated_migration_cycle(target, Mock(), Mock())
            drop_schema.assert_called_once_with(target)
            self.assertEqual(
                "postgresql+psycopg://user:password@127.0.0.1:5432/aegisqa",
                os.environ["AEGISQA_DATABASE_URL"],
            )
            self.assertNotIn("AEGISQA_ALEMBIC_SCHEMA", os.environ)


class _IsolatedProvisioningDatabase:
    """Test-only DatabaseManager-compatible session boundary for one owned schema."""

    configured = True

    def __init__(self, engine) -> None:
        self._sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @contextmanager
    def session_scope(self):
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class PostgreSQLIntegrationTest(unittest.TestCase):
    """Runs only against an explicitly configured disposable PostgreSQL test database."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.target = _prepare_isolated_test_target()
        cls.original_database_url, cls.original_schema = _activate_isolated_target(cls.target)
        try:
            command.upgrade(_alembic_config(), "head")
            cls.engine = _create_isolated_engine(cls.target)
        except Exception:
            try:
                _drop_isolated_schema(cls.target)
            finally:
                _restore_database_url(cls.original_database_url)
                _restore_alembic_schema(cls.original_schema)
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.engine.dispose()
            _drop_isolated_schema(cls.target)
        finally:
            _restore_database_url(cls.original_database_url)
            _restore_alembic_schema(cls.original_schema)

    def setUp(self) -> None:
        with self.engine.begin() as connection:
            # These tables exist only in this harness's generated search_path.
            # Delete dependents first; never use CASCADE/TRUNCATE.
            connection.execute(text("DELETE FROM execution_events"))
            connection.execute(text("DELETE FROM execution_attempts"))
            connection.execute(text("DELETE FROM execution_jobs"))
            connection.execute(text("DELETE FROM project_memberships"))
            connection.execute(text("DELETE FROM projects"))
            connection.execute(text("DELETE FROM users"))
        self.provisioner = UserProvisioner(_IsolatedProvisioningDatabase(self.engine))
        self.project_service = ProjectService(_IsolatedProvisioningDatabase(self.engine))
        self.execution_service = ExecutionService(_IsolatedProvisioningDatabase(self.engine))
        self.worker = ExecutionWorker(_IsolatedProvisioningDatabase(self.engine), "worker-a", 5)

    def _user(self, subject: str = "subject") -> User:
        return User(identity_subject=subject, email="user@example.test", display_name="User")

    def _principal(
        self, identity_subject: str = "oidc:issuer-a-subject-a", email: str = "user@example.test", display_name: str = "User"
    ) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal("https://issuer.example", "subject", identity_subject, email, display_name)

    def test_oidc_first_login_reuse_and_distinct_identities(self) -> None:
        first = self.provisioner.provision(self._principal())
        repeated = self.provisioner.provision(self._principal(email="updated@example.test"))
        distinct = self.provisioner.provision(self._principal("oidc:issuer-b-subject-a"))
        self.assertEqual(first.id, repeated.id)
        self.assertEqual("active", repeated.status)
        self.assertEqual("updated@example.test", repeated.email)
        self.assertNotEqual(first.id, distinct.id)
        with self.engine.connect() as connection:
            self.assertEqual(2, connection.execute(text("SELECT count(*) FROM users")).scalar_one())

    def test_oidc_disabled_user_is_never_reactivated(self) -> None:
        user = self.provisioner.provision(self._principal())
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE users SET status = 'disabled' WHERE id = :id"), {"id": user.id})
        with self.assertRaises(DisabledAccountError):
            self.provisioner.provision(self._principal(email="new@example.test"))
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT status, email FROM users WHERE id = :id"), {"id": user.id}).one()
        self.assertEqual("disabled", row[0])
        self.assertEqual("user@example.test", row[1])

    def test_oidc_concurrent_first_login_creates_one_user(self) -> None:
        principal = self._principal()
        with ThreadPoolExecutor(max_workers=4) as executor:
            user_ids = list(executor.map(lambda _: self.provisioner.provision(principal).id, range(4)))
        self.assertEqual(1, len(set(user_ids)))
        with self.engine.connect() as connection:
            self.assertEqual(1, connection.execute(text("SELECT count(*) FROM users")).scalar_one())

    def test_oidc_creation_and_update_failures_roll_back(self) -> None:
        invalid = self._principal(display_name="x" * 256)
        with self.assertRaises(DataError):
            self.provisioner.provision(invalid)
        with self.engine.connect() as connection:
            self.assertEqual(0, connection.execute(text("SELECT count(*) FROM users")).scalar_one())

        user = self.provisioner.provision(self._principal(display_name="Original"))
        with self.assertRaises(DataError):
            self.provisioner.provision(self._principal(display_name="x" * 256))
        with self.engine.connect() as connection:
            display_name = connection.execute(text("SELECT display_name FROM users WHERE id = :id"), {"id": user.id}).scalar_one()
        self.assertEqual("Original", display_name)

    def test_project_membership_grant_history_and_administrator_guards(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        operator = self.provisioner.provision(self._principal("oidc:operator"))
        project = self.project_service.create(administrator, "Project", "project")
        membership = self.project_service.grant(administrator, str(project.id), str(operator.id), "operator")
        changed = self.project_service.change_role(administrator, str(project.id), str(operator.id), "reviewer")
        self.assertNotEqual(membership.id, changed.id)
        self.assertEqual("reviewer", changed.role)
        with self.assertRaises(SafeApiError) as self_change:
            self.project_service.change_role(administrator, str(project.id), str(administrator.id), "reviewer")
        self.assertEqual("forbidden", self_change.exception.code)
        with self.assertRaises(SafeApiError) as final_admin:
            self.project_service.revoke(operator, str(project.id), str(administrator.id))
        self.assertEqual("forbidden", final_admin.exception.code)
        self.project_service.revoke(administrator, str(project.id), str(operator.id))
        history = self.project_service.memberships(administrator, str(project.id))
        self.assertEqual(3, len(history))

    def _execution_job(self, project_id, user_id, key="key-1", retry_of=None):
        return ExecutionJob(id=uuid.uuid4(), project_id=project_id, requested_by_user_id=user_id, request_schema_version="1.0.0", request_snapshot={"objective": "controlled"}, idempotency_key=key, request_fingerprint="a" * 64, status="queued", deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5), retry_of_execution_job_id=retry_of)

    def _execution_row(self, value):
        row = {}
        for column in value.__table__.columns:
            attribute = "event_metadata" if column.name == "metadata" else column.name
            field = getattr(value, attribute)
            if field is not None:
                row[column.name] = field
        return row

    def test_execution_persistence_constraints_and_project_isolation(self) -> None:
        user = self.provisioner.provision(self._principal())
        project = self.project_service.create(user, "Execution A", "execution-a")
        other = self.project_service.create(user, "Execution B", "execution-b")
        job = self._execution_job(project.id, user.id)
        with self.engine.begin() as connection:
            connection.execute(ExecutionJob.__table__.insert(), [self._execution_row(job)])
            attempt = ExecutionAttempt(id=uuid.uuid4(), project_id=project.id, execution_job_id=job.id, attempt_number=1, status="queued")
            connection.execute(ExecutionAttempt.__table__.insert(), [self._execution_row(attempt)])
            event = ExecutionEvent(id=uuid.uuid4(), project_id=project.id, execution_job_id=job.id, execution_attempt_id=attempt.id, correlation_id="correlation", category="queued", event_metadata={"safe": True})
            connection.execute(ExecutionEvent.__table__.insert(), [self._execution_row(event)])
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(ExecutionAttempt.__table__.insert(), [{"id": uuid.uuid4(), "project_id": project.id, "execution_job_id": job.id, "attempt_number": 1, "status": "queued"}])
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(ExecutionAttempt.__table__.insert(), [{"id": uuid.uuid4(), "project_id": other.id, "execution_job_id": job.id, "attempt_number": 1, "status": "queued"}])
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(ExecutionJob.__table__.insert(), [self._execution_row(self._execution_job(project.id, user.id))])
        with self.engine.begin() as connection:
            connection.execute(ExecutionJob.__table__.insert(), [self._execution_row(self._execution_job(other.id, user.id))])

    def test_execution_status_snapshot_retry_event_and_rollback_constraints(self) -> None:
        user = self.provisioner.provision(self._principal())
        project = self.project_service.create(user, "Execution C", "execution-c")
        parent = self._execution_job(project.id, user.id, "parent")
        child = self._execution_job(project.id, user.id, "child", parent.id)
        with self.engine.begin() as connection:
            connection.execute(ExecutionJob.__table__.insert(), [self._execution_row(parent)])
            connection.execute(ExecutionJob.__table__.insert(), [self._execution_row(child)])
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(ExecutionJob.__table__.insert(), [self._execution_row(self._execution_job(project.id, user.id, "child-2", parent.id))])
        for values in (("bad", {"objective": "x"}), ("queued", {}), ("queued", {"objective": "x"}, "bad")):
            with self.assertRaises(IntegrityError):
                with self.engine.begin() as connection:
                    job = self._execution_job(project.id, user.id, str(uuid.uuid4()))
                    job.status, job.request_snapshot = values[0], values[1]
                    if len(values) == 3: job.request_schema_version = values[2]
                    connection.execute(ExecutionJob.__table__.insert(), [self._execution_row(job)])
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(ExecutionEvent.__table__.insert(), [{"id": uuid.uuid4(), "project_id": project.id, "execution_job_id": parent.id, "correlation_id": "", "category": "queued", "metadata": []}])
        with self.engine.connect() as connection:
            self.assertEqual(2, connection.execute(text("SELECT count(*) FROM execution_jobs")).scalar_one())

    def test_execution_cleanup_is_safe_for_the_next_test(self) -> None:
        user = self.provisioner.provision(self._principal())
        project = self.project_service.create(user, "Execution Cleanup", "execution-cleanup")
        job = self._execution_job(project.id, user.id, "cleanup")
        with self.engine.begin() as connection:
            connection.execute(ExecutionJob.__table__.insert(), [self._execution_row(job)])
        self.setUp()
        with self.engine.connect() as connection:
            self.assertEqual(0, connection.execute(text("SELECT count(*) FROM execution_jobs")).scalar_one())
            self.assertEqual(0, connection.execute(text("SELECT count(*) FROM projects")).scalar_one())

    def test_execution_service_submission_idempotency_and_cancellation(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        reviewer = self.provisioner.provision(self._principal("oidc:reviewer"))
        project = self.project_service.create(administrator, "Execution API", "execution-api")
        self.project_service.grant(administrator, str(project.id), str(reviewer.id), "reviewer")
        request = {
            "schema_version": "1.0.0",
            "deadline_seconds": 60,
            "orchestration": {"objective": "controlled"},
        }
        job, attempt, replayed = self.execution_service.submit(
            administrator, str(project.id), "submit-key", request
        )
        with self.engine.connect() as connection:
            self.assertEqual(1, connection.execute(text("SELECT count(*) FROM execution_jobs")).scalar_one())
            self.assertEqual(1, connection.execute(text("SELECT count(*) FROM execution_attempts")).scalar_one())
            self.assertEqual(1, connection.execute(text("SELECT count(*) FROM execution_events")).scalar_one())
        repeated, repeated_attempt, was_replayed = self.execution_service.submit(
            administrator, str(project.id), "submit-key", request
        )
        self.assertFalse(replayed)
        self.assertTrue(was_replayed)
        self.assertEqual(job.id, repeated.id)
        self.assertEqual(attempt.id, repeated_attempt.id)
        with self.assertRaises(SafeApiError) as conflict:
            self.execution_service.submit(
                administrator,
                str(project.id),
                "submit-key",
                {"schema_version": "1.0.0", "deadline_seconds": 61, "orchestration": {}},
            )
        self.assertEqual("idempotency_conflict", conflict.exception.code)
        with self.assertRaises(SafeApiError) as forbidden:
            self.execution_service.submit(reviewer, str(project.id), "reviewer-key", request)
        self.assertEqual("forbidden", forbidden.exception.code)
        cancelled = self.execution_service.cancel(administrator, str(project.id), str(job.id))
        self.assertEqual("cancelled", cancelled.status)
        same_cancel = self.execution_service.cancel(administrator, str(project.id), str(job.id))
        self.assertEqual("cancelled", same_cancel.status)
        detail, detail_attempt, events = self.execution_service.detail(
            administrator, str(project.id), str(job.id)
        )
        self.assertEqual("cancelled", detail.status)
        self.assertEqual("cancelled", detail_attempt.status)
        self.assertEqual(["execution_submitted", "execution_cancelled"], [event.category for event in events])

    def test_execution_service_retry_is_linear_and_project_scoped(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        project = self.project_service.create(administrator, "Retry API", "retry-api")
        other = self.project_service.create(administrator, "Other API", "other-api")
        request = {"schema_version": "1.0.0", "deadline_seconds": 60, "orchestration": {}}
        parent, _, _ = self.execution_service.submit(administrator, str(project.id), "parent-key", request)
        self.execution_service.cancel(administrator, str(project.id), str(parent.id))
        child, child_attempt, replayed = self.execution_service.retry(
            administrator, str(project.id), str(parent.id), "retry-key"
        )
        repeated, repeated_attempt, was_replayed = self.execution_service.retry(
            administrator, str(project.id), str(parent.id), "retry-key"
        )
        self.assertFalse(replayed)
        self.assertTrue(was_replayed)
        self.assertEqual(parent.id, child.retry_of_execution_job_id)
        self.assertEqual(child.id, repeated.id)
        self.assertEqual(child_attempt.id, repeated_attempt.id)
        with self.assertRaises(SafeApiError) as duplicate:
            self.execution_service.retry(administrator, str(project.id), str(parent.id), "second-retry")
        self.assertEqual("retry_already_exists", duplicate.exception.code)
        with self.assertRaises(SafeApiError) as hidden:
            self.execution_service.detail(administrator, str(other.id), str(parent.id))
        self.assertEqual("not_found", hidden.exception.code)

    def test_worker_claim_heartbeat_fencing_and_no_false_success(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        project = self.project_service.create(administrator, "Worker", "worker")
        request = {"schema_version": "1.0.0", "deadline_seconds": 60, "orchestration": {}}
        job, attempt, _ = self.execution_service.submit(administrator, str(project.id), "worker-key", request)
        claim = self.worker.claim_next()
        self.assertIsNotNone(claim)
        self.assertEqual(attempt.id, claim.attempt_id)
        self.assertTrue(self.worker.heartbeat(claim))
        stale = ClaimedAttempt(claim.attempt_id, claim.job_id, claim.project_id, "wrong-token")
        self.assertFalse(self.worker.heartbeat(stale))
        self.assertTrue(self.worker.begin(claim))
        self.assertTrue(self.worker.finalize_failed(claim, "executor_unavailable"))
        detail, final_attempt, events = self.execution_service.detail(administrator, str(project.id), str(job.id))
        self.assertEqual("failed", detail.status)
        self.assertEqual("failed", final_attempt.status)
        self.assertEqual("executor_unavailable", final_attempt.terminal_outcome)
        self.assertIn("execution_claimed", [event.category for event in events])
        self.assertNotEqual("succeeded", detail.status)

    def test_worker_claims_distinct_jobs_and_sweeps_once(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        project = self.project_service.create(administrator, "Worker Sweep", "worker-sweep")
        request = {"schema_version": "1.0.0", "deadline_seconds": 60, "orchestration": {}}
        first, _, _ = self.execution_service.submit(administrator, str(project.id), "worker-first", request)
        second, _, _ = self.execution_service.submit(administrator, str(project.id), "worker-second", request)
        other_worker = ExecutionWorker(_IsolatedProvisioningDatabase(self.engine), "worker-b", 5)
        first_claim = self.worker.claim_next()
        second_claim = other_worker.claim_next()
        self.assertNotEqual(first_claim.attempt_id, second_claim.attempt_id)
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE execution_attempts SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'")
            )
        self.assertEqual(2, self.worker.sweep())
        self.assertEqual(0, self.worker.sweep())
        with self.engine.connect() as connection:
            self.assertEqual(2, connection.execute(text("SELECT count(*) FROM execution_jobs WHERE status = 'recovery_required'")).scalar_one())

    def test_worker_sweeps_expired_queued_deadline(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        project = self.project_service.create(administrator, "Worker Deadline", "worker-deadline")
        request = {"schema_version": "1.0.0", "deadline_seconds": 1, "orchestration": {}}
        job, _, _ = self.execution_service.submit(administrator, str(project.id), "deadline-key", request)
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE execution_jobs SET deadline_at = CURRENT_TIMESTAMP - INTERVAL '1 second'")
            )
        self.assertEqual(1, self.worker.sweep())
        detail, attempt, events = self.execution_service.detail(administrator, str(project.id), str(job.id))
        self.assertEqual("timed_out", detail.status)
        self.assertEqual("timed_out", attempt.status)
        self.assertIn("execution_timed_out", [event.category for event in events])

    def test_worker_sweeps_active_deadline_before_lease_loss(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        project = self.project_service.create(administrator, "Worker Active Deadline", "worker-active-deadline")
        request = {"schema_version": "1.0.0", "deadline_seconds": 60, "orchestration": {}}
        job, _, _ = self.execution_service.submit(administrator, str(project.id), "active-deadline-key", request)
        claim = self.worker.claim_next()
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE execution_jobs SET deadline_at = CURRENT_TIMESTAMP - INTERVAL '1 second'")
            )
        self.assertEqual(1, self.worker.sweep())
        detail, attempt, _ = self.execution_service.detail(administrator, str(project.id), str(job.id))
        self.assertEqual("timed_out", detail.status)
        self.assertEqual("timed_out", attempt.status)

    def test_concurrent_submission_and_claim_are_single_owner(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        project = self.project_service.create(administrator, "Worker Concurrency", "worker-concurrency")
        request = {"schema_version": "1.0.0", "deadline_seconds": 60, "orchestration": {}}
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _: self.execution_service.submit(administrator, str(project.id), "concurrent-key", request),
                range(2),
            ))
        self.assertEqual(1, len({result[0].id for result in results}))
        self.assertEqual(1, sum(1 for _, _, replayed in results if replayed))
        first_worker = ExecutionWorker(_IsolatedProvisioningDatabase(self.engine), "worker-c", 5)
        second_worker = ExecutionWorker(_IsolatedProvisioningDatabase(self.engine), "worker-d", 5)
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda worker: worker.claim_next(), (first_worker, second_worker)))
        self.assertEqual(1, sum(claim is not None for claim in claims))
        with self.engine.connect() as connection:
            self.assertEqual(1, connection.execute(text("SELECT count(*) FROM execution_jobs")).scalar_one())
            self.assertEqual(1, connection.execute(text("SELECT count(*) FROM execution_events WHERE category = 'execution_claimed'")).scalar_one())

    def test_worker_claim_rolls_back_when_event_write_fails(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        project = self.project_service.create(administrator, "Worker Atomicity", "worker-atomicity")
        request = {"schema_version": "1.0.0", "deadline_seconds": 60, "orchestration": {}}
        job, _, _ = self.execution_service.submit(administrator, str(project.id), "atomicity-key", request)
        with patch.object(self.worker, "_event", side_effect=RuntimeError("event write failed")):
            with self.assertRaises(RuntimeError):
                self.worker.claim_next()
        detail, attempt, events = self.execution_service.detail(administrator, str(project.id), str(job.id))
        self.assertEqual("queued", detail.status)
        self.assertEqual("queued", attempt.status)
        self.assertEqual(["execution_submitted"], [event.category for event in events])

    def test_active_cancellation_is_finalized_only_by_lease_holder(self) -> None:
        administrator = self.provisioner.provision(self._principal())
        project = self.project_service.create(administrator, "Worker Cancel", "worker-cancel")
        request = {"schema_version": "1.0.0", "deadline_seconds": 60, "orchestration": {}}
        job, _, _ = self.execution_service.submit(administrator, str(project.id), "cancel-key", request)
        claim = self.worker.claim_next()
        self.execution_service.cancel(administrator, str(project.id), str(job.id))
        self.assertTrue(self.worker.begin(claim))
        self.assertTrue(self.worker.cancel_requested(claim))
        self.assertTrue(self.worker.finalize_cancelled(claim))
        detail, attempt, _ = self.execution_service.detail(administrator, str(project.id), str(job.id))
        self.assertEqual("cancelled", detail.status)
        self.assertEqual("cancelled", attempt.status)

    def test_unique_identity_subject_and_project_foreign_key(self) -> None:
        from sqlalchemy.orm import Session

        with Session(self.engine) as session:
            first = self._user("same-subject")
            session.add(first)
            session.commit()
            session.add(self._user("same-subject"))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            session.add(Project(name="Broken", slug="broken", created_by_user_id=uuid.uuid4()))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            project = Project(name="Valid", slug="valid", created_by_user_id=first.id)
            session.add(project)
            session.commit()
            session.add(
                ProjectMembership(
                    project_id=project.id,
                    user_id=uuid.uuid4(),
                    role=MembershipRole.REVIEWER.value,
                    created_by_user_id=first.id,
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()

    def test_membership_role_active_uniqueness_and_revocation(self) -> None:
        from sqlalchemy.orm import Session

        with Session(self.engine) as session:
            user = self._user()
            session.add(user)
            session.flush()
            project = Project(name="Project", slug="project", created_by_user_id=user.id)
            session.add(project)
            session.flush()
            membership = ProjectMembership(
                project_id=project.id,
                user_id=user.id,
                role=MembershipRole.ADMINISTRATOR.value,
                created_by_user_id=user.id,
            )
            session.add(membership)
            session.commit()

            session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=MembershipRole.OPERATOR.value, created_by_user_id=user.id))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            membership = session.get(ProjectMembership, membership.id)
            membership.revoked_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            session.commit()
            session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=MembershipRole.REVIEWER.value, created_by_user_id=user.id))
            session.commit()

            session.add(ProjectMembership(project_id=project.id, user_id=user.id, role="invalid", created_by_user_id=user.id))
            with self.assertRaises(IntegrityError):
                session.commit()


class PostgreSQLMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade(self) -> None:
        target = _prepare_isolated_test_target()

        def verify_upgraded(engine) -> None:
            self.assertTrue({"users", "projects", "project_memberships"}.issubset(inspect(engine).get_table_names()))

        def verify_downgraded(engine) -> None:
            self.assertFalse({"users", "projects", "project_memberships"}.intersection(inspect(engine).get_table_names()))

        _run_isolated_migration_cycle(target, verify_upgraded, verify_downgraded)

    def test_execution_constraint_reconciliation_from_20261120_01(self) -> None:
        target = _prepare_isolated_test_target()
        original_database_url, original_schema = _activate_isolated_target(target)
        engine = None
        try:
            config = _alembic_config()
            command.upgrade(config, "20261120_01")
            command.upgrade(config, "head")
            engine = _create_isolated_engine(target)
            names = {item["name"] for item in inspect(engine).get_check_constraints("execution_jobs")}
            self.assertTrue({"ck_execution_jobs_request_schema_version", "ck_execution_jobs_request_nonempty"}.issubset(names))
        finally:
            if engine is not None:
                engine.dispose()
            try:
                _drop_isolated_schema(target)
            finally:
                _restore_database_url(original_database_url)
                _restore_alembic_schema(original_schema)
