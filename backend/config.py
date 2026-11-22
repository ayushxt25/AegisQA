"""Typed, environment-based configuration for the backend foundation."""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from sqlalchemy.engine import make_url


_ENVIRONMENTS = ("development", "test", "production")
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
_DEFAULT_TRUSTED_HOSTS = ("localhost", "127.0.0.1", "testserver")
_SERVICE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class ConfigurationError(ValueError):
    """Raised when an environment configuration is unsafe or invalid."""


class BackendSettings(BaseModel):
    """Validated configuration with intentionally small Phase 1 surface area."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    service_name: str = "aegisqa-api"
    trusted_hosts: Tuple[str, ...] = _DEFAULT_TRUSTED_HOSTS
    database_url: Optional[str] = None
    database_pool_size: int = 5
    database_max_overflow: int = 2
    database_pool_timeout_seconds: int = 10
    database_connect_timeout_seconds: int = 3
    oidc_issuer: Optional[str] = None
    oidc_audience: Optional[str] = None
    oidc_jwks_url: Optional[str] = None
    oidc_jwks_timeout_seconds: int = 3
    oidc_jwks_cache_seconds: int = 300
    execution_worker_id: Optional[str] = None
    execution_worker_poll_seconds: int = 2
    execution_worker_lease_seconds: int = 30
    execution_worker_heartbeat_seconds: int = 10
    execution_worker_recovery_limit: int = 100
    fixture_execution_enabled: bool = False
    fixture_sandbox_image_id: Optional[str] = None
    fixture_image_id: Optional[str] = None

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ENVIRONMENTS:
            raise ValueError("environment is not supported")
        return normalized

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _LOG_LEVELS:
            raise ValueError("log level is not supported")
        return normalized

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        normalized = value.strip()
        if not _SERVICE_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("service name is invalid")
        return normalized

    @field_validator("trusted_hosts")
    @classmethod
    def validate_trusted_hosts(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        if not value:
            raise ValueError("trusted hosts must not be empty")
        normalized = tuple(host.strip() for host in value)
        for host in normalized:
            if not host or host == "*" or not _HOST_PATTERN.fullmatch(host):
                raise ValueError("trusted hosts contain an unsafe host")
        return normalized

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        try:
            url = make_url(value.strip())
        except Exception as error:
            raise ValueError("database URL is invalid") from error
        if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
            raise ValueError("database URL must use PostgreSQL psycopg")
        return value.strip()

    @field_validator(
        "database_pool_size",
        "database_max_overflow",
        "database_pool_timeout_seconds",
        "database_connect_timeout_seconds",
    )
    @classmethod
    def validate_database_limits(cls, value: int) -> int:
        if value < 1 or value > 60:
            raise ValueError("database limit is outside the permitted range")
        return value

    @field_validator("oidc_issuer", "oidc_jwks_url")
    @classmethod
    def validate_oidc_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("OIDC URL is invalid")
        parsed = urlparse(value.strip())
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("OIDC URL is invalid")
        return value.strip()

    @field_validator("oidc_audience")
    @classmethod
    def validate_oidc_audience(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not value.strip() or len(value.strip()) > 255:
            raise ValueError("OIDC audience is invalid")
        return value.strip()

    @field_validator("oidc_jwks_timeout_seconds", "oidc_jwks_cache_seconds")
    @classmethod
    def validate_oidc_limits(cls, value: int) -> int:
        if value < 1 or value > 3600:
            raise ValueError("OIDC limit is outside the permitted range")
        return value

    @field_validator("execution_worker_id")
    @classmethod
    def validate_worker_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not _SERVICE_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("worker ID is invalid")
        return normalized

    @field_validator("fixture_sandbox_image_id", "fixture_image_id")
    @classmethod
    def validate_fixture_image_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        if not _IMAGE_ID_PATTERN.fullmatch(value.strip()):
            raise ValueError("fixture image ID is invalid")
        return value.strip()

    @field_validator("execution_worker_poll_seconds")
    @classmethod
    def validate_worker_poll(cls, value: int) -> int:
        if not 1 <= value <= 60:
            raise ValueError("worker poll interval is invalid")
        return value

    @field_validator("execution_worker_lease_seconds")
    @classmethod
    def validate_worker_lease(cls, value: int) -> int:
        if not 5 <= value <= 3600:
            raise ValueError("worker lease duration is invalid")
        return value

    @field_validator("execution_worker_heartbeat_seconds")
    @classmethod
    def validate_worker_heartbeat(cls, value: int) -> int:
        if not 1 <= value <= 1800:
            raise ValueError("worker heartbeat interval is invalid")
        return value

    @field_validator("execution_worker_recovery_limit")
    @classmethod
    def validate_worker_recovery_limit(cls, value: int) -> int:
        if not 1 <= value <= 1000:
            raise ValueError("worker recovery limit is invalid")
        return value

    @model_validator(mode="after")
    def reject_production_debug(self) -> "BackendSettings":
        if self.environment == "production" and self.debug:
            raise ValueError("debug cannot be enabled in production")
        if self.environment == "production" and self.database_url is None:
            raise ValueError("production requires an explicit database URL")
        oidc_values = (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)
        if any(oidc_values) and not all(oidc_values):
            raise ValueError("OIDC configuration must be complete")
        if self.environment == "production" and not all(oidc_values):
            raise ValueError("production requires OIDC configuration")
        if self.environment != "test" and any(value and value.startswith("http://") for value in (self.oidc_issuer, self.oidc_jwks_url)):
            raise ValueError("OIDC URLs must use HTTPS outside test")
        if self.execution_worker_heartbeat_seconds >= self.execution_worker_lease_seconds:
            raise ValueError("worker heartbeat must be shorter than lease duration")
        fixture_images = (self.fixture_sandbox_image_id, self.fixture_image_id)
        if self.fixture_execution_enabled:
            if self.environment == "production" or not all(fixture_images):
                raise ValueError("fixture execution is not configured safely")
        elif any(fixture_images):
            raise ValueError("fixture images require explicit fixture execution opt-in")
        return self

    @property
    def oidc_configured(self) -> bool:
        return self.oidc_issuer is not None


def load_settings(environ: Optional[Mapping[str, str]] = None) -> BackendSettings:
    """Load validated settings without printing environment values or secrets."""

    values = os.environ if environ is None else environ
    environment = values.get("AEGISQA_ENVIRONMENT", "development").strip().lower()
    debug = _parse_boolean(values.get("AEGISQA_DEBUG", "false"), "AEGISQA_DEBUG")
    log_level = values.get("AEGISQA_LOG_LEVEL", "INFO").strip().upper()
    service_name = values.get("AEGISQA_SERVICE_NAME", "aegisqa-api").strip()
    trusted_hosts = _parse_trusted_hosts(values.get("AEGISQA_TRUSTED_HOSTS"))
    database_url = values.get("AEGISQA_DATABASE_URL")
    pool_size = _parse_integer(values.get("AEGISQA_DATABASE_POOL_SIZE", "5"), "AEGISQA_DATABASE_POOL_SIZE")
    max_overflow = _parse_integer(values.get("AEGISQA_DATABASE_MAX_OVERFLOW", "2"), "AEGISQA_DATABASE_MAX_OVERFLOW")
    pool_timeout = _parse_integer(values.get("AEGISQA_DATABASE_POOL_TIMEOUT_SECONDS", "10"), "AEGISQA_DATABASE_POOL_TIMEOUT_SECONDS")
    connect_timeout = _parse_integer(values.get("AEGISQA_DATABASE_CONNECT_TIMEOUT_SECONDS", "3"), "AEGISQA_DATABASE_CONNECT_TIMEOUT_SECONDS")
    jwks_timeout = _parse_integer(values.get("AEGISQA_OIDC_JWKS_TIMEOUT_SECONDS", "3"), "AEGISQA_OIDC_JWKS_TIMEOUT_SECONDS")
    jwks_cache = _parse_integer(values.get("AEGISQA_OIDC_JWKS_CACHE_SECONDS", "300"), "AEGISQA_OIDC_JWKS_CACHE_SECONDS")
    worker_poll = _parse_integer(values.get("AEGISQA_WORKER_POLL_SECONDS", "2"), "AEGISQA_WORKER_POLL_SECONDS")
    worker_lease = _parse_integer(values.get("AEGISQA_WORKER_LEASE_SECONDS", "30"), "AEGISQA_WORKER_LEASE_SECONDS")
    worker_heartbeat = _parse_integer(values.get("AEGISQA_WORKER_HEARTBEAT_SECONDS", "10"), "AEGISQA_WORKER_HEARTBEAT_SECONDS")
    worker_recovery = _parse_integer(values.get("AEGISQA_WORKER_RECOVERY_LIMIT", "100"), "AEGISQA_WORKER_RECOVERY_LIMIT")
    fixture_execution = _parse_boolean(values.get("AEGISQA_FIXTURE_EXECUTION_ENABLED", "false"), "AEGISQA_FIXTURE_EXECUTION_ENABLED")

    try:
        return BackendSettings(
            environment=environment,
            debug=debug,
            log_level=log_level,
            service_name=service_name,
            trusted_hosts=trusted_hosts,
            database_url=database_url,
            database_pool_size=pool_size,
            database_max_overflow=max_overflow,
            database_pool_timeout_seconds=pool_timeout,
            database_connect_timeout_seconds=connect_timeout,
            oidc_issuer=values.get("AEGISQA_OIDC_ISSUER"),
            oidc_audience=values.get("AEGISQA_OIDC_AUDIENCE"),
            oidc_jwks_url=values.get("AEGISQA_OIDC_JWKS_URL"),
            oidc_jwks_timeout_seconds=jwks_timeout,
            oidc_jwks_cache_seconds=jwks_cache,
            execution_worker_id=values.get("AEGISQA_WORKER_ID"),
            execution_worker_poll_seconds=worker_poll,
            execution_worker_lease_seconds=worker_lease,
            execution_worker_heartbeat_seconds=worker_heartbeat,
            execution_worker_recovery_limit=worker_recovery,
            fixture_execution_enabled=fixture_execution,
            fixture_sandbox_image_id=values.get("AEGISQA_FIXTURE_SANDBOX_IMAGE_ID"),
            fixture_image_id=values.get("AEGISQA_FIXTURE_IMAGE_ID"),
        )
    except ValidationError as error:
        raise ConfigurationError("Backend configuration is invalid.") from error


def _parse_boolean(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ConfigurationError("{0} must be true or false.".format(name))


def _parse_trusted_hosts(value: Optional[str]) -> Tuple[str, ...]:
    if value is None:
        return _DEFAULT_TRUSTED_HOSTS

    hosts = tuple(item.strip() for item in value.split(","))
    return hosts


def _parse_integer(value: str, name: str) -> int:
    try:
        return int(value.strip())
    except ValueError as error:
        raise ConfigurationError("{0} must be an integer.".format(name)) from error
