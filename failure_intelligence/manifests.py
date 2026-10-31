"""Typed, versioned P0.4C execution and evaluation manifest contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Optional, Tuple

from .contracts import ConfidenceLevel, FailureCategory, canonical_json


MANIFEST_SCHEMA_VERSION = "1.0.0"
_ARTIFACT_ROOT = ("artifacts", "p0.4c")


class ScenarioId(str, Enum):
    F01 = "F01"
    F02 = "F02"
    F03 = "F03"
    F04 = "F04"
    F05 = "F05"
    F06 = "F06"


def _require_identifier(value: str, field_name: str, reject_separators: bool = False) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("{0} must be a non-empty string of at most 128 characters.".format(field_name))
    if reject_separators and ("/" in value or "\\" in value):
        raise ValueError("{0} must not contain path separators.".format(field_name))


def _raw_path_segments(value: str, field_name: str) -> str:
    """Reject lexical traversal and empty segments before path normalization."""
    if not isinstance(value, str) or not value:
        raise ValueError("{0} must be a non-empty relative path.".format(field_name))
    normalized = value.replace("\\", "/")
    if any(segment in ("", ".", "..") for segment in normalized.split("/")):
        raise ValueError("{0} must not contain empty or traversal path segments.".format(field_name))
    return normalized


def _artifact_path(value: str, field_name: str, require_file: bool = False) -> str:
    normalized = _raw_path_segments(value, field_name)
    path = PurePosixPath(normalized)
    if path.is_absolute() or ":" in path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("{0} must be a safe relative path.".format(field_name))
    if path.parts[:2] != _ARTIFACT_ROOT or len(path.parts) < 3:
        raise ValueError("{0} must be under artifacts/p0.4c/.".format(field_name))
    if require_file and path.suffix != ".json":
        raise ValueError("{0} must name a JSON file.".format(field_name))
    return path.as_posix()


def _fixture_path(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = _raw_path_segments(value, "fixture_path")
    path = PurePosixPath(normalized)
    if (path.is_absolute() or ":" in path.parts or path.parts[:1] != ("fixture",)
            or len(path.parts) < 2 or path.suffix != ".html"):
        raise ValueError("fixture_path must remain under fixture/.")
    return path.as_posix()


@dataclass(frozen=True)
class ManifestActionPolicy:
    allowed_urls: Tuple[str, ...]
    allowed_click_targets: Tuple[str, ...]
    allowed_fill_targets: Tuple[str, ...] = ()
    allowed_inspect_targets: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "allowed_urls", "allowed_click_targets", "allowed_fill_targets", "allowed_inspect_targets",
        ):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or not all(isinstance(item, str) and item for item in values):
                raise ValueError("{0} must be a tuple of non-empty strings.".format(field_name))
            object.__setattr__(self, field_name, tuple(sorted(set(values))))

    def to_dict(self) -> dict:
        return {
            "allowed_click_targets": list(self.allowed_click_targets),
            "allowed_fill_targets": list(self.allowed_fill_targets),
            "allowed_inspect_targets": list(self.allowed_inspect_targets),
            "allowed_urls": list(self.allowed_urls),
        }


@dataclass(frozen=True)
class F05RepeatPlan:
    """Execution-only trial identity plan; it contains no outcome or answer key."""

    boundary_run_id: str
    control_run_id: str
    boundary_attempt_ids: Tuple[str, ...]
    control_attempt_ids: Tuple[str, ...]
    equivalence_key: str

    def __post_init__(self) -> None:
        _require_identifier(self.boundary_run_id, "boundary_run_id", reject_separators=True)
        _require_identifier(self.control_run_id, "control_run_id", reject_separators=True)
        _require_identifier(self.equivalence_key, "equivalence_key")
        if self.boundary_run_id == self.control_run_id:
            raise ValueError("F05 control and boundary runs must be separate.")
        for field_name in ("boundary_attempt_ids", "control_attempt_ids"):
            attempts = getattr(self, field_name)
            if not isinstance(attempts, tuple) or len(attempts) != 10:
                raise ValueError("{0} must contain exactly ten attempts.".format(field_name))
            if not all(isinstance(item, str) and item for item in attempts):
                raise ValueError("{0} must contain non-empty attempt IDs.".format(field_name))
            if len(set(attempts)) != len(attempts):
                raise ValueError("{0} must not contain duplicate attempt IDs.".format(field_name))
        if set(self.boundary_attempt_ids) & set(self.control_attempt_ids):
            raise ValueError("F05 control and boundary attempt IDs must be distinct.")

    def to_dict(self) -> dict:
        return {
            "boundary_attempt_ids": list(self.boundary_attempt_ids),
            "boundary_run_id": self.boundary_run_id,
            "control_attempt_ids": list(self.control_attempt_ids),
            "control_run_id": self.control_run_id,
            "equivalence_key": self.equivalence_key,
        }


@dataclass(frozen=True)
class ExecutionManifest:
    """Pre-browser execution configuration with no ground-truth classification data."""

    scenario_id: ScenarioId
    run_id: str
    artifact_directory: str
    action_policy: ManifestActionPolicy
    timeout_ms: int
    fixture_path: Optional[str] = None
    server_route: Optional[str] = None
    f05_repeat_plan: Optional[F05RepeatPlan] = None
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, ScenarioId):
            raise TypeError("scenario_id must be a ScenarioId.")
        _require_identifier(self.run_id, "run_id", reject_separators=True)
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("Unsupported manifest schema_version.")
        if not isinstance(self.action_policy, ManifestActionPolicy):
            raise TypeError("action_policy must be a ManifestActionPolicy.")
        if not isinstance(self.timeout_ms, int) or not 1 <= self.timeout_ms <= 60_000:
            raise ValueError("timeout_ms must be between 1 and 60000.")
        object.__setattr__(self, "fixture_path", _fixture_path(self.fixture_path))
        object.__setattr__(self, "artifact_directory", _artifact_path(self.artifact_directory, "artifact_directory"))
        expected_directory = "artifacts/p0.4c/{0}".format(self.run_id)
        if self.artifact_directory != expected_directory:
            raise ValueError("artifact_directory must be the investigation run directory.")
        if self.scenario_id is ScenarioId.F04:
            if self.fixture_path is not None or not isinstance(self.server_route, str) or not self.server_route.startswith("/"):
                raise ValueError("F04 requires a local server route and no fixture_path.")
        elif self.fixture_path is None or self.server_route is not None:
            raise ValueError("Non-F04 scenarios require a fixture_path and no server_route.")
        if self.scenario_id is ScenarioId.F05:
            if not isinstance(self.f05_repeat_plan, F05RepeatPlan):
                raise ValueError("F05 requires an F05RepeatPlan.")
            if self.run_id != self.f05_repeat_plan.boundary_run_id:
                raise ValueError("F05 manifest run_id must be the boundary investigation run.")
        elif self.f05_repeat_plan is not None:
            raise ValueError("Only F05 may define an F05RepeatPlan.")

    def to_dict(self) -> dict:
        return {
            "action_policy": self.action_policy.to_dict(),
            "artifact_directory": self.artifact_directory,
            "f05_repeat_plan": self.f05_repeat_plan.to_dict() if self.f05_repeat_plan else None,
            "fixture_path": self.fixture_path,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id.value,
            "schema_version": self.schema_version,
            "server_route": self.server_route,
            "timeout_ms": self.timeout_ms,
        }

    def normalized_json(self) -> str:
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class EvaluationExpectation:
    scenario_id: ScenarioId
    expected_category: FailureCategory
    expected_confidence: ConfidenceLevel

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, ScenarioId):
            raise TypeError("scenario_id must be a ScenarioId.")
        if not isinstance(self.expected_category, FailureCategory):
            raise TypeError("expected_category must be a FailureCategory.")
        if not isinstance(self.expected_confidence, ConfidenceLevel):
            raise TypeError("expected_confidence must be a ConfidenceLevel.")

    def to_dict(self) -> dict:
        return {
            "expected_category": self.expected_category.value,
            "expected_confidence": self.expected_confidence.value,
            "scenario_id": self.scenario_id.value,
        }


@dataclass(frozen=True)
class EvaluationManifest:
    """Evaluation-only answer key; never a classifier or execution-runner input."""

    manifest_path: str
    expectations: Tuple[EvaluationExpectation, ...]
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("Unsupported manifest schema_version.")
        object.__setattr__(self, "manifest_path", _artifact_path(self.manifest_path, "manifest_path", require_file=True))
        if not isinstance(self.expectations, tuple):
            raise TypeError("expectations must be a tuple.")
        if not all(isinstance(item, EvaluationExpectation) for item in self.expectations):
            raise TypeError("expectations must contain EvaluationExpectation values.")
        scenario_ids = tuple(item.scenario_id for item in self.expectations)
        if set(scenario_ids) != set(ScenarioId) or len(scenario_ids) != len(ScenarioId):
            raise ValueError("Evaluation manifest must contain each F01-F06 scenario exactly once.")
        object.__setattr__(self, "expectations", tuple(sorted(self.expectations, key=lambda item: item.scenario_id.value)))

    def to_dict(self) -> dict:
        return {
            "expectations": [item.to_dict() for item in self.expectations],
            "manifest_path": self.manifest_path,
            "schema_version": self.schema_version,
        }

    def normalized_json(self) -> str:
        return canonical_json(self.to_dict())
