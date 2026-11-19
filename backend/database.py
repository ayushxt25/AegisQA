"""Synchronous SQLAlchemy engine and transaction boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.config import BackendSettings


def redact_database_url(database_url: str) -> str:
    """Return a usable diagnostic form without revealing credentials."""

    return make_url(database_url).render_as_string(hide_password=True)


class DatabaseManager:
    """Owns one application engine and creates short-lived sessions on demand."""

    def __init__(self, settings: BackendSettings) -> None:
        self._settings = settings
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker[Session]] = None

    @property
    def configured(self) -> bool:
        return self._settings.database_url is not None

    @property
    def engine(self) -> Optional[Engine]:
        return self._engine

    def start(self) -> None:
        """Construct, but do not implicitly query through, the configured engine."""

        if not self.configured or self._engine is not None:
            return
        self._engine = create_engine(
            self._settings.database_url,
            pool_pre_ping=True,
            pool_size=self._settings.database_pool_size,
            max_overflow=self._settings.database_max_overflow,
            pool_timeout=self._settings.database_pool_timeout_seconds,
            pool_recycle=1800,
            connect_args={
                "connect_timeout": self._settings.database_connect_timeout_seconds
            },
        )
        self._session_factory = sessionmaker(
            bind=self._engine, autoflush=False, expire_on_commit=False
        )

    def check_connection(self) -> bool:
        """Run a bounded PostgreSQL health query and return no connection details."""

        if not self.configured:
            return True
        self.start()
        try:
            assert self._engine is not None
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Provide one committed/rolled-back/closed session per unit of work."""

        self.start()
        if self._session_factory is None:
            raise RuntimeError("Database is not configured.")
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Release pooled connections during controlled application shutdown."""

        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._session_factory = None
