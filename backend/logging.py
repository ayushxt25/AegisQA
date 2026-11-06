"""Structured, intentionally narrow backend logging."""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional, TextIO

from backend.config import BackendSettings


class JsonFormatter(logging.Formatter):
    """Emit only allowlisted log fields to avoid accidental request-data logging."""

    _EXTRA_FIELDS = (
        "event",
        "request_id",
        "method",
        "route",
        "status_code",
        "duration_ms",
        "error_type",
        "environment",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self._EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure_logging(
    settings: BackendSettings,
    stream: Optional[TextIO] = None,
    logger_name: Optional[str] = None,
) -> logging.Logger:
    """Configure one isolated application logger without configuration values."""

    name = logger_name or "aegisqa.api.{0}".format(uuid.uuid4().hex)
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.log_level))
    logger.propagate = False

    for handler in tuple(logger.handlers):
        if getattr(handler, "_aegisqa_handler", False):
            logger.removeHandler(handler)

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler._aegisqa_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger
