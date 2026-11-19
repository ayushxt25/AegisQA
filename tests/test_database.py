"""PostgreSQL-only tests for the P1.2 persistence foundation."""

from __future__ import annotations

import os
import re
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple
from unittest.mock import MagicMock, Mock, patch

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from backend.app import create_app
from backend.config import BackendSettings, ConfigurationError, load_settings
from backend.database import DatabaseManager, redact_database_url
from backend.models import MembershipRole, Project, ProjectMembership, User


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
            connection.execute(text("DELETE FROM project_memberships"))
            connection.execute(text("DELETE FROM projects"))
            connection.execute(text("DELETE FROM users"))

    def _user(self, subject: str = "subject") -> User:
        return User(identity_subject=subject, email="user@example.test", display_name="User")

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
