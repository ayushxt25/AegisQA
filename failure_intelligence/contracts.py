"""Sanitized, typed, deterministic evidence contracts for P0.4B.1."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit


SCHEMA_VERSION = "1.0.0"
MAX_STRING_LENGTH = 256
MAX_COLLECTION_ITEMS = 20
MAX_MAPPING_ITEMS = 16
MAX_NESTING_DEPTH = 3
MAX_SERIALIZED_EVIDENCE_BYTES = 4096


class EvidenceSource(str, Enum):
    EXECUTION_LOG = "execution_log"
    OBSERVATION = "observation"
    ACTION = "action"
    SCREENSHOT = "screenshot"
    TRACE = "trace"
    NETWORK = "network"
    ENVIRONMENT = "environment"


class FailureStage(str, Enum):
    PLANNING = "planning"
    VALIDATION = "validation"
    EXECUTION = "execution"
    ASSERTION = "assertion"
    ENVIRONMENT = "environment"


class FailureCategory(str, Enum):
    APPLICATION_DEFECT = "application_defect"
    AUTOMATION_FAILURE = "automation_failure"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    FLAKY_OR_INTERMITTENT = "flaky_or_intermittent"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ConfidenceLevel(str, Enum):
    VERIFIED = "verified"
    EVIDENCE_SUPPORTED_INFERENCE = "evidence_supported_inference"
    HYPOTHESIS = "hypothesis"


class ReproducibilityStatus(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    REPRODUCED = "reproduced"
    NOT_REPRODUCED = "not_reproduced"
    INCONCLUSIVE = "inconclusive"


class SanitizationStatus(str, Enum):
    SANITIZED = "sanitized"


class SanitizationError(ValueError):
    """Raised when facts are not safe for the P0.4 evidence boundary."""


_ALLOWED_FACT_FIELDS = frozenset(
    {
        "action_kind",
        "available",
        "duration_ms",
        "enabled",
        "error",
        "http_status",
        "injected_delay_ms",
        "outcome",
        "permitted_elements",
        "result",
        "role",
        "selector",
        "status",
        "tag",
        "target",
        "timeout_ms",
        "title",
        "url",
        "visible",
    }
)
_ALLOWED_NESTED_FIELDS = frozenset(
    {"available", "enabled", "permitted_actions", "role", "selector", "tag", "visible"}
)
_FORBIDDEN_KEY_FRAGMENTS = (
    "api_key", "authorization", "body", "cookie", "credential", "html", "input_value",
    "password", "secret", "token",
)
_SAFE_ERROR_CATEGORIES = frozenset(
    {
        "assertion_failed",
        "execution_failed",
        "model_unavailable",
        "provider_error",
        "rate_limited",
        "timeout",
        "unavailable",
        "validation_rejected",
    }
)
_OMITTED_UNTRUSTED_TEXT = "[untrusted_text_omitted]"


def _require_enum(value: Any, enum_type: Any, field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise TypeError("{0} must be a {1}.".format(field_name, enum_type.__name__))


def _require_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("{0} must be a non-empty string of at most 128 characters.".format(field_name))


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("captured_at must be a timezone-aware datetime.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme:
        raise SanitizationError("url must include a scheme.")
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = "{0}:{1}".format(host, parsed.port)
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))[:MAX_STRING_LENGTH]


def _validate_key(key: Any, allowed: frozenset) -> str:
    if not isinstance(key, str):
        raise SanitizationError("Evidence field names must be strings.")
    lowered = key.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS):
        raise SanitizationError("Sensitive evidence field is not permitted.")
    if key not in allowed:
        raise SanitizationError("Evidence field is not allowlisted.")
    return key


def _sanitize_text(value: str, field_name: Optional[str]) -> str:
    if field_name in ("result", "title"):
        # Browser-controlled free text is not needed as raw evidence at this stage.
        return _OMITTED_UNTRUSTED_TEXT if value else ""
    if field_name == "error":
        if value not in _SAFE_ERROR_CATEGORIES:
            raise SanitizationError("Error evidence must use a safe category.")
        return value
    return value[:MAX_STRING_LENGTH]


def _sanitize_value(value: Any, depth: int, nested: bool = False, field_name: Optional[str] = None) -> Any:
    if depth > MAX_NESTING_DEPTH:
        raise SanitizationError("Evidence nesting exceeds the permitted limit.")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise SanitizationError("Evidence numbers must be finite JSON values.")
        return value
    if isinstance(value, str):
        return _sanitize_text(value, field_name)
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise SanitizationError("Evidence collection exceeds the permitted limit.")
        return [_sanitize_value(item, depth + 1, nested=True) for item in value]
    if isinstance(value, Mapping):
        if len(value) > MAX_MAPPING_ITEMS:
            raise SanitizationError("Evidence mapping exceeds the permitted limit.")
        allowed = _ALLOWED_NESTED_FIELDS if nested else _ALLOWED_FACT_FIELDS
        sanitized = {}
        for key in sorted(value):
            safe_key = _validate_key(key, allowed)
            sanitized[safe_key] = _sanitize_value(
                value[key], depth + 1, nested=True, field_name=safe_key
            )
        return sanitized
    raise SanitizationError("Evidence value is not JSON-compatible.")


def sanitize_observed_facts(facts: Mapping[str, Any]) -> Dict[str, Any]:
    """Return bounded allowlisted facts; reject sensitive or unbounded structures."""
    if not isinstance(facts, Mapping):
        raise SanitizationError("observed_facts must be a mapping.")
    sanitized = _sanitize_value(facts, depth=0)
    if "url" in sanitized:
        if not isinstance(sanitized["url"], str):
            raise SanitizationError("url must be a string.")
        sanitized["url"] = _sanitize_url(sanitized["url"])
    if len(canonical_json(sanitized).encode("utf-8")) > MAX_SERIALIZED_EVIDENCE_BYTES:
        raise SanitizationError("Serialized evidence exceeds the permitted limit.")
    return sanitized


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    return value


def canonical_json(value: Mapping[str, Any]) -> str:
    """Serialize already sanitized JSON data reproducibly for comparison and hashing."""
    return json.dumps(_json_copy(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    run_id: str
    source: EvidenceSource
    producing_component: str
    captured_at: datetime
    observed_facts: Mapping[str, Any]
    sanitization_status: SanitizationStatus = SanitizationStatus.SANITIZED
    artifact_reference: Optional[str] = None
    content_digest: Optional[str] = None

    def __post_init__(self) -> None:
        _require_id(self.evidence_id, "evidence_id")
        _require_id(self.run_id, "run_id")
        _require_enum(self.source, EvidenceSource, "source")
        _require_enum(self.sanitization_status, SanitizationStatus, "sanitization_status")
        _require_id(self.producing_component, "producing_component")
        if self.artifact_reference is not None:
            if not isinstance(self.artifact_reference, str) or self.artifact_reference.startswith(("/", "\\")) or ".." in self.artifact_reference:
                raise ValueError("artifact_reference must be a safe relative path.")
        if self.content_digest is not None and (len(self.content_digest) != 64 or any(char not in "0123456789abcdef" for char in self.content_digest)):
            raise ValueError("content_digest must be a lowercase SHA-256 digest.")
        object.__setattr__(self, "observed_facts", _freeze_json(sanitize_observed_facts(self.observed_facts)))
        _timestamp(self.captured_at)

    def digest_payload(self) -> Dict[str, Any]:
        return {
            "artifact_reference": self.artifact_reference,
            "captured_at": _timestamp(self.captured_at),
            "evidence_id": self.evidence_id,
            "observed_facts": _json_copy(self.observed_facts),
            "producing_component": self.producing_component,
            "run_id": self.run_id,
            "sanitization_status": self.sanitization_status.value,
            "schema_version": SCHEMA_VERSION,
            "source": self.source.value,
        }

    def finalized(self) -> "EvidenceRecord":
        digest = hashlib.sha256(canonical_json(self.digest_payload()).encode("utf-8")).hexdigest()
        return replace(self, content_digest=digest)

    def verify_digest(self) -> bool:
        if self.content_digest is None:
            return False
        expected = hashlib.sha256(canonical_json(self.digest_payload()).encode("utf-8")).hexdigest()
        return hmac.compare_digest(self.content_digest, expected)

    def to_dict(self) -> Dict[str, Any]:
        payload = self.digest_payload()
        payload["content_digest"] = self.content_digest
        return _json_copy(payload)

    def normalized_json(self) -> str:
        return canonical_json(self.to_dict())


class EvidenceIdFactory:
    """Collector-owned IDs and timestamps; no model contributes identifiers."""

    def __init__(self, run_id: Optional[str] = None, clock: Optional[Callable[[], datetime]] = None) -> None:
        self.run_id = run_id or "run_{0}".format(uuid.uuid4().hex)
        _require_id(self.run_id, "run_id")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sequence = 0

    def create(
        self, source: EvidenceSource, producing_component: str, observed_facts: Mapping[str, Any],
        artifact_reference: Optional[str] = None,
    ) -> EvidenceRecord:
        self._sequence += 1
        return EvidenceRecord(
            evidence_id="ev_{0}_{1:06d}".format(self.run_id, self._sequence),
            run_id=self.run_id,
            source=source,
            producing_component=producing_component,
            captured_at=self._clock(),
            observed_facts=observed_facts,
            artifact_reference=artifact_reference,
        ).finalized()


@dataclass(frozen=True)
class FailureEvent:
    failure_id: str
    run_id: str
    occurred_at: datetime
    stage: FailureStage
    observed_facts: Mapping[str, Any]
    evidence_ids: Tuple[str, ...]
    initial_category: Optional[FailureCategory] = None

    def __post_init__(self) -> None:
        _require_id(self.failure_id, "failure_id")
        _require_id(self.run_id, "run_id")
        _require_enum(self.stage, FailureStage, "stage")
        if self.initial_category is not None:
            _require_enum(self.initial_category, FailureCategory, "initial_category")
        if not self.evidence_ids or not all(isinstance(item, str) and item for item in self.evidence_ids):
            raise ValueError("FailureEvent requires evidence_ids.")
        object.__setattr__(self, "observed_facts", _freeze_json(sanitize_observed_facts(self.observed_facts)))
        _timestamp(self.occurred_at)

    def to_dict(self) -> Dict[str, Any]:
        return {"evidence_ids": list(self.evidence_ids), "failure_id": self.failure_id,
                "initial_category": self.initial_category.value if self.initial_category else None,
                "occurred_at": _timestamp(self.occurred_at), "observed_facts": _json_copy(self.observed_facts),
                "run_id": self.run_id, "stage": self.stage.value}


@dataclass(frozen=True)
class InvestigationFinding:
    finding_id: str
    classification: FailureCategory
    confidence: ConfidenceLevel
    observed_facts: Tuple[str, ...]
    supporting_evidence_ids: Tuple[str, ...]
    contradictory_evidence_ids: Tuple[str, ...]
    uncertainty: str
    missing_evidence: Tuple[str, ...]
    reproducibility: ReproducibilityStatus

    def __post_init__(self) -> None:
        _require_id(self.finding_id, "finding_id")
        _require_enum(self.classification, FailureCategory, "classification")
        _require_enum(self.confidence, ConfidenceLevel, "confidence")
        _require_enum(self.reproducibility, ReproducibilityStatus, "reproducibility")
        if self.confidence is ConfidenceLevel.VERIFIED and not self.supporting_evidence_ids:
            raise ValueError("VERIFIED findings require supporting evidence.")

    def to_dict(self) -> Dict[str, Any]:
        return {"classification": self.classification.value, "confidence": self.confidence.value,
                "contradictory_evidence_ids": list(self.contradictory_evidence_ids), "finding_id": self.finding_id,
                "missing_evidence": list(self.missing_evidence), "observed_facts": list(self.observed_facts),
                "reproducibility": self.reproducibility.value, "supporting_evidence_ids": list(self.supporting_evidence_ids),
                "uncertainty": self.uncertainty[:MAX_STRING_LENGTH]}


@dataclass(frozen=True)
class InvestigationReport:
    report_id: str
    run_id: str
    created_at: datetime
    failure_event_ids: Tuple[str, ...]
    findings: Tuple[InvestigationFinding, ...]
    deterministic_summary: str
    overall_confidence: ConfidenceLevel
    unresolved_questions: Tuple[str, ...]
    llm_summary: Optional[str] = None
    contract_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_id(self.report_id, "report_id")
        _require_id(self.run_id, "run_id")
        _require_enum(self.overall_confidence, ConfidenceLevel, "overall_confidence")
        if self.contract_version != SCHEMA_VERSION:
            raise ValueError("Unsupported contract_version.")
        _timestamp(self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract_version": self.contract_version, "created_at": _timestamp(self.created_at),
                "deterministic_summary": self.deterministic_summary[:MAX_STRING_LENGTH],
                "failure_event_ids": list(self.failure_event_ids),
                "findings": [finding.to_dict() for finding in self.findings],
                "llm_summary": self.llm_summary[:MAX_STRING_LENGTH] if self.llm_summary else None,
                "overall_confidence": self.overall_confidence.value, "report_id": self.report_id,
                "run_id": self.run_id, "unresolved_questions": list(self.unresolved_questions)}

    def normalized_json(self) -> str:
        return canonical_json(self.to_dict())
