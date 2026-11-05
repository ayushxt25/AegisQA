"""Minimal local Playwright evidence collector for controlled P0.4C fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

from agent_runtime.actions import ActionKind, ActionPolicy, BrowserAction, ActionValidationError, validate_action
from agent_runtime.browser import PlaywrightBrowserTool
from agent_runtime.runner import validate_runtime_action
from agent_runtime.state import Observation

from .contracts import (
    EvidenceIdFactory,
    EvidenceRecord,
    EvidenceSource,
    FailureEvent,
    FailureStage,
    canonical_json,
)
from .manifests import ExecutionManifest, ScenarioId


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "p0.4c"
ASSERTION_TIMEOUT_MS = 1_000


class BrowserCollectionError(RuntimeError):
    """Safe collector error that never includes browser or page exception text."""


@dataclass(frozen=True)
class BrowserCollectionResult:
    failure_event: FailureEvent
    evidence: Tuple[EvidenceRecord, ...]
    artifact_directory: Path
    assertion_passed: Optional[bool]


def _action_policy(manifest: ExecutionManifest) -> ActionPolicy:
    policy = manifest.action_policy
    return ActionPolicy(
        allowed_urls=set(policy.allowed_urls),
        allowed_click_targets=set(policy.allowed_click_targets),
        allowed_fill_targets=set(policy.allowed_fill_targets),
        allowed_inspect_targets=set(policy.allowed_inspect_targets),
    )


def _artifact_directory(manifest: ExecutionManifest, project_root: Path) -> Path:
    artifact_root = (project_root / "artifacts" / "p0.4c").resolve()
    target = (project_root / manifest.artifact_directory).resolve()
    try:
        target.relative_to(artifact_root)
    except ValueError as error:
        raise BrowserCollectionError("Manifest artifact directory is outside the P0.4C artifact root.") from error
    if target.name != manifest.run_id:
        raise BrowserCollectionError("Manifest artifact directory does not match its run ID.")
    return target


def _fixture_url(manifest: ExecutionManifest, project_root: Path) -> str:
    if manifest.fixture_path is None:
        raise BrowserCollectionError("This collector supports local fixture scenarios only.")
    fixture = (project_root / manifest.fixture_path).resolve()
    fixture_root = (project_root / "fixture").resolve()
    try:
        fixture.relative_to(fixture_root)
    except ValueError as error:
        raise BrowserCollectionError("Manifest fixture is outside the controlled fixture directory.") from error
    if not fixture.is_file():
        raise BrowserCollectionError("Controlled fixture is unavailable.")
    return fixture.as_uri()


def _safe_observation_facts(observation: Observation) -> dict:
    """Map browser data to the existing evidence allowlist; never retain page text."""
    permitted = []
    for element in observation.data.get("permitted_elements", []):
        if not isinstance(element, dict):
            continue
        facts = {}
        for key in ("available", "enabled", "role", "selector", "tag", "visible"):
            if key in element:
                facts[key] = element[key]
        if facts:
            permitted.append(facts)
    facts = {"permitted_elements": permitted}
    if isinstance(observation.data.get("url"), str):
        facts["url"] = observation.data["url"]
    return facts


def _scenario_action(manifest: ExecutionManifest) -> BrowserAction:
    if manifest.scenario_id is ScenarioId.F01:
        return BrowserAction(ActionKind.CLICK, "#missing")
    return BrowserAction(ActionKind.CLICK, "#increment")


def _stage(manifest: ExecutionManifest) -> FailureStage:
    if manifest.scenario_id in (ScenarioId.F01, ScenarioId.F02):
        return FailureStage.VALIDATION
    return FailureStage.ASSERTION


def _write_artifacts(directory: Path, evidence: Sequence[EvidenceRecord], event: FailureEvent) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "evidence.json").write_text(
        canonical_json({"evidence": [record.to_dict() for record in evidence]}), encoding="utf-8"
    )
    (directory / "failure-event.json").write_text(canonical_json(event.to_dict()), encoding="utf-8")


def collect_browser_evidence(
    manifest: ExecutionManifest, project_root: Path = PROJECT_ROOT,
) -> BrowserCollectionResult:
    """Collect one bounded local fixture run without invoking classification or an LLM."""
    if manifest.scenario_id is ScenarioId.F04:
        raise BrowserCollectionError("F04 requires the later local HTTP server implementation.")
    artifact_directory = _artifact_directory(manifest, project_root)
    fixture_url = _fixture_url(manifest, project_root)
    policy = _action_policy(manifest)
    factory = EvidenceIdFactory(run_id=manifest.run_id)
    evidence = []
    assertion_passed: Optional[bool] = None

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCollectionError("Playwright is not installed.") from error

    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=manifest.timeout_ms)
            context = browser.new_context()
            context.set_default_timeout(manifest.timeout_ms)
            context.set_default_navigation_timeout(manifest.timeout_ms)
            page = context.new_page()
            page.goto(fixture_url, wait_until="load", timeout=manifest.timeout_ms)
            browser_tool = PlaywrightBrowserTool(page, policy)
            observation = browser_tool.observe()
            evidence.append(factory.create(EvidenceSource.OBSERVATION, "browser", _safe_observation_facts(observation)))

            action = _scenario_action(manifest)
            try:
                validate_action(action, policy)
                validate_runtime_action(action, policy, observation)
            except ActionValidationError:
                evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
                    "action_kind": action.kind.value,
                    "error": "validation_rejected",
                    "selector": action.target,
                }))
            else:
                try:
                    page.locator(action.target).click(timeout=manifest.timeout_ms)
                except PlaywrightError:
                    evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
                        "action_kind": action.kind.value,
                        "error": "execution_failed",
                        "selector": action.target,
                    }))
                    assertion_passed = False
                else:
                    evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
                        "action_kind": action.kind.value,
                        "outcome": "executed",
                        "selector": action.target,
                    }))
                    try:
                        page.wait_for_function(
                            "document.getElementById('result').textContent === 'Clicks: 1'",
                            timeout=min(manifest.timeout_ms, ASSERTION_TIMEOUT_MS),
                        )
                        assertion_passed = True
                    except PlaywrightTimeoutError:
                        assertion_passed = False
                    evidence.append(factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {
                        "status": "assertion_passed" if assertion_passed else "assertion_failed",
                    }))
    except PlaywrightError as error:
        raise BrowserCollectionError("Playwright browser collection failed.") from error
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    event = FailureEvent(
        failure_id="failure_{0}".format(manifest.scenario_id.value.lower()),
        run_id=manifest.run_id,
        occurred_at=evidence[-1].captured_at,
        stage=_stage(manifest),
        observed_facts={"status": "failed" if assertion_passed is not True else "completed"},
        evidence_ids=tuple(record.evidence_id for record in evidence),
    )
    _write_artifacts(artifact_directory, evidence, event)
    return BrowserCollectionResult(event, tuple(evidence), artifact_directory, assertion_passed)
