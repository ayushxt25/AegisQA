"""Typed, environment-based configuration for the backend foundation."""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator
from sqlalchemy.engine import make_url


_ENVIRONMENTS = ("development", "test", "production")
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
_DEFAULT_TRUSTED_HOSTS = ("localhost", "127.0.0.1", "testserver")
_SERVICE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")


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

    @model_validator(mode="after")
    def reject_production_debug(self) -> "BackendSettings":
        if self.environment == "production" and self.debug:
            raise ValueError("debug cannot be enabled in production")
        if self.environment == "production" and self.database_url is None:
            raise ValueError("production requires an explicit database URL")
        return self


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
