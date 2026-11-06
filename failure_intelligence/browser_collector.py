"""Minimal local Playwright evidence collector for controlled P0.4C fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from threading import Thread
from time import perf_counter
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
from .classifier import (
    ClassificationResult,
    DeterministicClassifier,
    InjectedCondition,
    RepeatedAttempt,
    TrustedInfrastructureContext,
)
from .manifests import ExecutionManifest, ScenarioId


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "p0.4c"
ASSERTION_TIMEOUT_MS = 1_000
F05_ASSERTION_TIMEOUT_MS = 100
F05_CONTROL_DELAY_MS = 0
F05_BOUNDARY_DELAYS_MS = (25, 300, 25, 300, 25, 300, 25, 300, 25, 300)


class BrowserCollectionError(RuntimeError):
    """Safe collector error that never includes browser or page exception text."""


@dataclass(frozen=True)
class BrowserCollectionResult:
    failure_event: FailureEvent
    evidence: Tuple[EvidenceRecord, ...]
    artifact_directory: Path
    assertion_passed: Optional[bool]
    infrastructure_context: Optional[TrustedInfrastructureContext] = None


@dataclass(frozen=True)
class F05TrialMeasurement:
    attempt_id: str
    injected_delay_ms: int
    action_duration_ms: Optional[int]
    assertion_duration_ms: Optional[int]
    assertion_passed: Optional[bool]
    assertion_evidence_id: Optional[str]

    def to_dict(self) -> dict:
        return {
            "action_duration_ms": self.action_duration_ms,
            "assertion_duration_ms": self.assertion_duration_ms,
            "assertion_evidence_id": self.assertion_evidence_id,
            "assertion_passed": self.assertion_passed,
            "attempt_id": self.attempt_id,
            "injected_delay_ms": self.injected_delay_ms,
        }


@dataclass(frozen=True)
class F05EvaluationResult:
    control_event: FailureEvent
    boundary_event: FailureEvent
    control_evidence: Tuple[EvidenceRecord, ...]
    boundary_evidence: Tuple[EvidenceRecord, ...]
    control_trials: Tuple[F05TrialMeasurement, ...]
    boundary_trials: Tuple[F05TrialMeasurement, ...]
    repeated_attempts: Tuple[RepeatedAttempt, ...]
    classification: ClassificationResult

    @staticmethod
    def _metrics(trials: Sequence[F05TrialMeasurement]) -> dict:
        completed = [trial for trial in trials if trial.assertion_passed is not None]
        durations = [trial.assertion_duration_ms for trial in completed if trial.assertion_duration_ms is not None]
        return {
            "attempts": len(trials),
            "completed": len(completed),
            "failed": sum(trial.assertion_passed is False for trial in completed),
            "passed": sum(trial.assertion_passed is True for trial in completed),
            "assertion_duration_ms": {
                "max": max(durations) if durations else None,
                "median": median(durations) if durations else None,
                "min": min(durations) if durations else None,
            },
        }

    def to_dict(self) -> dict:
        return {
            "boundary": {"metrics": self._metrics(self.boundary_trials),
                         "run_id": self.boundary_event.run_id,
                         "trials": [trial.to_dict() for trial in self.boundary_trials]},
            "classification": {"applied_rule_id": self.classification.applied_rule_id,
                               "finding": self.classification.finding.to_dict()},
            "control": {"metrics": self._metrics(self.control_trials),
                        "run_id": self.control_event.run_id,
                        "trials": [trial.to_dict() for trial in self.control_trials]},
            "repeated_attempts": [
                {"assertion_passed": attempt.assertion_passed, "attempt_id": attempt.attempt_id,
                 "evidence_ids": list(attempt.evidence_ids), "equivalence_key": attempt.equivalence_key}
                for attempt in self.repeated_attempts
            ],
        }


@dataclass(frozen=True)
class F06EvaluationResult:
    failure_event: FailureEvent
    evidence: Tuple[EvidenceRecord, ...]
    missing_assertion_evidence_id: str
    artifact_directory: Path
    classification: ClassificationResult

    def to_dict(self) -> dict:
        return {
            "classification": {"applied_rule_id": self.classification.applied_rule_id,
                               "finding": self.classification.finding.to_dict()},
            "missing_assertion_evidence_id": self.missing_assertion_evidence_id,
            "run_id": self.failure_event.run_id,
        }


class _ServiceUnavailableHandler(BaseHTTPRequestHandler):
    """Local deterministic endpoint; no request/response data is persisted."""

    def do_GET(self):  # noqa: N802 - stdlib handler API name
        if self.path != self.route:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        return


class Loopback503Server:
    """Bounded lifecycle for the F04 loopback-only service failure injection."""

    def __init__(self, route: str) -> None:
        self.route = route
        handler = type("_ConfiguredServiceUnavailableHandler", (_ServiceUnavailableHandler,), {"route": route})
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except OSError as error:
            raise BrowserCollectionError("Unable to start the loopback HTTP server.") from error
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return "http://{0}:{1}{2}".format(host, port, self.route)

    def __enter__(self) -> "Loopback503Server":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.server.shutdown()
        self.thread.join(timeout=1.0)
        self.server.server_close()
        if self.thread.is_alive() and exc_type is None:
            raise BrowserCollectionError("Loopback server did not stop within its bounded shutdown timeout.")


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


def _collect_f04(
    manifest: ExecutionManifest, artifact_directory: Path, factory: EvidenceIdFactory,
) -> BrowserCollectionResult:
    """Navigate Chromium to a local injected 503 and record status-only network evidence."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCollectionError("Playwright is not installed.") from error

    evidence = []
    browser = None
    context = None
    try:
        with Loopback503Server(manifest.server_route) as server:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, timeout=manifest.timeout_ms)
                context = browser.new_context()
                context.set_default_timeout(manifest.timeout_ms)
                context.set_default_navigation_timeout(manifest.timeout_ms)
                page = context.new_page()
                started_at = perf_counter()
                response = page.goto(server.url, wait_until="commit", timeout=manifest.timeout_ms)
                duration_ms = int((perf_counter() - started_at) * 1000)
                if response is None or response.status != 503:
                    raise BrowserCollectionError("Loopback service did not return the expected HTTP status.")
                evidence.append(factory.create(EvidenceSource.NETWORK, "http_server", {
                    "duration_ms": duration_ms,
                    "http_status": 503,
                    "timeout_ms": manifest.timeout_ms,
                    "url": server.url,
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
        failure_id="failure_f04", run_id=manifest.run_id, occurred_at=evidence[0].captured_at,
        stage=FailureStage.ENVIRONMENT, observed_facts={"status": "failed"},
        evidence_ids=tuple(record.evidence_id for record in evidence),
    )
    infrastructure_context = TrustedInfrastructureContext(
        "context_{0}".format(manifest.run_id), manifest.run_id, InjectedCondition.SIMULATED_HTTP_503,
    )
    _write_artifacts(artifact_directory, evidence, event)
    return BrowserCollectionResult(event, tuple(evidence), artifact_directory, None, infrastructure_context)


def collect_browser_evidence(
    manifest: ExecutionManifest, project_root: Path = PROJECT_ROOT,
) -> BrowserCollectionResult:
    """Collect one bounded local fixture run without invoking classification or an LLM."""
    artifact_directory = _artifact_directory(manifest, project_root)
    factory = EvidenceIdFactory(run_id=manifest.run_id)
    if manifest.scenario_id is ScenarioId.F04:
        return _collect_f04(manifest, artifact_directory, factory)
    fixture_url = _fixture_url(manifest, project_root)
    policy = _action_policy(manifest)
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


def _group_artifact_directory(project_root: Path, run_id: str) -> Path:
    target = (project_root / "artifacts" / "p0.4c" / run_id).resolve()
    root = (project_root / "artifacts" / "p0.4c").resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise BrowserCollectionError("F05 artifact directory is outside the P0.4C artifact root.") from error
    return target


def _run_f05_trial(
    browser, fixture_url: str, policy: ActionPolicy, factory: EvidenceIdFactory,
    attempt_id: str, profile: str, delay_ms: int, timeout_ms: int,
) -> Tuple[F05TrialMeasurement, Tuple[EvidenceRecord, ...]]:
    """Run one fresh-context F05 trial and record only actual browser outcomes."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    evidence = []
    context = None
    action_duration_ms = None
    assertion_duration_ms = None
    assertion_passed = None
    assertion_evidence_id = None
    try:
        context = browser.new_context()
        context.set_default_timeout(timeout_ms)
        context.set_default_navigation_timeout(timeout_ms)
        page = context.new_page()
        page.goto(fixture_url, wait_until="load", timeout=timeout_ms)
        page.evaluate(
            "config => window.aegisqaConfigureRenderingDelay(config.profile, config.delayMs)",
            {"profile": profile, "delayMs": delay_ms},
        )
        observation = PlaywrightBrowserTool(page, policy).observe()
        evidence.append(factory.create(EvidenceSource.OBSERVATION, "browser", _safe_observation_facts(observation)))
        action = BrowserAction(ActionKind.CLICK, "#increment")
        try:
            validate_action(action, policy)
            validate_runtime_action(action, policy, observation)
        except ActionValidationError:
            evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
                "action_kind": action.kind.value, "error": "validation_rejected", "selector": action.target,
            }))
        else:
            started_at = perf_counter()
            page.locator(action.target).click(timeout=timeout_ms)
            action_duration_ms = int((perf_counter() - started_at) * 1000)
            evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
                "action_kind": action.kind.value, "duration_ms": action_duration_ms,
                "outcome": "executed", "selector": action.target,
            }))
            started_at = perf_counter()
            try:
                page.wait_for_function(
                    "document.getElementById('result').textContent === 'Clicks: 1'",
                    timeout=F05_ASSERTION_TIMEOUT_MS,
                )
                assertion_passed = True
            except PlaywrightTimeoutError:
                assertion_passed = False
            assertion_duration_ms = int((perf_counter() - started_at) * 1000)
            assertion = factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {
                "duration_ms": assertion_duration_ms,
                "injected_delay_ms": delay_ms,
                "status": "assertion_passed" if assertion_passed else "assertion_failed",
                "timeout_ms": F05_ASSERTION_TIMEOUT_MS,
            })
            evidence.append(assertion)
            assertion_evidence_id = assertion.evidence_id
    except PlaywrightError:
        evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
            "action_kind": "click", "error": "execution_failed", "selector": "#increment",
        }))
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
    return (
        F05TrialMeasurement(
            attempt_id=attempt_id, injected_delay_ms=delay_ms, action_duration_ms=action_duration_ms,
            assertion_duration_ms=assertion_duration_ms, assertion_passed=assertion_passed,
            assertion_evidence_id=assertion_evidence_id,
        ),
        tuple(evidence),
    )


def _f05_event(run_id: str, evidence: Sequence[EvidenceRecord], group: str) -> FailureEvent:
    completed = any(record.observed_facts.get("status") == "assertion_passed" for record in evidence)
    return FailureEvent(
        failure_id="failure_f05_{0}".format(group), run_id=run_id,
        occurred_at=evidence[-1].captured_at, stage=FailureStage.ASSERTION,
        observed_facts={"status": "completed" if completed else "failed"},
        evidence_ids=tuple(record.evidence_id for record in evidence),
    )


def _write_f05_report(directory: Path, group: str, trials: Sequence[F05TrialMeasurement],
                      classification: Optional[ClassificationResult] = None) -> None:
    payload = {"group": group, "trials": [trial.to_dict() for trial in trials]}
    if classification is not None:
        payload["classification"] = {
            "applied_rule_id": classification.applied_rule_id,
            "finding": classification.finding.to_dict(),
        }
    (directory / "f05-results.json").write_text(canonical_json(payload), encoding="utf-8")


def collect_f05_repeated_trials(
    manifest: ExecutionManifest, project_root: Path = PROJECT_ROOT,
) -> F05EvaluationResult:
    """Run the fixed F05 control/boundary protocol and classify observed boundary outcomes."""
    if manifest.scenario_id is not ScenarioId.F05 or manifest.f05_repeat_plan is None:
        raise BrowserCollectionError("F05 repeated trials require an F05 execution manifest.")
    plan = manifest.f05_repeat_plan
    if len(F05_BOUNDARY_DELAYS_MS) != len(plan.boundary_attempt_ids):
        raise BrowserCollectionError("F05 boundary schedule does not match the manifest attempt count.")
    artifact_directory = _artifact_directory(manifest, project_root)
    control_directory = _group_artifact_directory(project_root, plan.control_run_id)
    fixture_url = _fixture_url(manifest, project_root)
    policy = _action_policy(manifest)
    boundary_factory = EvidenceIdFactory(run_id=manifest.run_id)
    control_factory = EvidenceIdFactory(run_id=plan.control_run_id)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserCollectionError("Playwright is not installed.") from error

    control_trials = []
    boundary_trials = []
    control_evidence = []
    boundary_evidence = []
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=manifest.timeout_ms)
            for attempt_id in plan.control_attempt_ids:
                trial, records = _run_f05_trial(
                    browser, fixture_url, policy, control_factory, attempt_id, "control",
                    F05_CONTROL_DELAY_MS, manifest.timeout_ms,
                )
                control_trials.append(trial)
                control_evidence.extend(records)
            for attempt_id, delay_ms in zip(plan.boundary_attempt_ids, F05_BOUNDARY_DELAYS_MS):
                trial, records = _run_f05_trial(
                    browser, fixture_url, policy, boundary_factory, attempt_id, "boundary",
                    delay_ms, manifest.timeout_ms,
                )
                boundary_trials.append(trial)
                boundary_evidence.extend(records)
    except PlaywrightError as error:
        raise BrowserCollectionError("Playwright browser collection failed.") from error
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    control_event = _f05_event(plan.control_run_id, control_evidence, "control")
    boundary_event = _f05_event(manifest.run_id, boundary_evidence, "boundary")
    candidate_attempts = tuple(
        RepeatedAttempt(trial.attempt_id, plan.equivalence_key, trial.assertion_passed, (trial.assertion_evidence_id,))
        for trial in boundary_trials
        if trial.assertion_passed is not None and trial.assertion_evidence_id is not None
    )
    repeated_attempts = candidate_attempts if len(candidate_attempts) == len(plan.boundary_attempt_ids) else ()
    classification = DeterministicClassifier().classify(
        boundary_event, tuple(boundary_evidence), repeated_attempts=repeated_attempts,
    )
    _write_artifacts(control_directory, control_evidence, control_event)
    _write_artifacts(artifact_directory, boundary_evidence, boundary_event)
    _write_f05_report(control_directory, "control", control_trials)
    _write_f05_report(artifact_directory, "boundary", boundary_trials, classification)
    return F05EvaluationResult(
        control_event=control_event, boundary_event=boundary_event,
        control_evidence=tuple(control_evidence), boundary_evidence=tuple(boundary_evidence),
        control_trials=tuple(control_trials), boundary_trials=tuple(boundary_trials),
        repeated_attempts=repeated_attempts, classification=classification,
    )


def collect_f06_ambiguous_evidence(
    manifest: ExecutionManifest, project_root: Path = PROJECT_ROOT,
) -> F06EvaluationResult:
    """Run a real click, then model an unavailable post-action assertion without inventing it."""
    if manifest.scenario_id is not ScenarioId.F06:
        raise BrowserCollectionError("F06 ambiguous collection requires an F06 execution manifest.")
    artifact_directory = _artifact_directory(manifest, project_root)
    fixture_url = _fixture_url(manifest, project_root)
    policy = _action_policy(manifest)
    factory = EvidenceIdFactory(run_id=manifest.run_id)
    evidence = []

    try:
        from playwright.sync_api import Error as PlaywrightError
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
            observation = PlaywrightBrowserTool(page, policy).observe()
            evidence.append(factory.create(EvidenceSource.OBSERVATION, "browser", _safe_observation_facts(observation)))
            action = BrowserAction(ActionKind.CLICK, "#increment")
            try:
                validate_action(action, policy)
                validate_runtime_action(action, policy, observation)
            except ActionValidationError:
                evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
                    "action_kind": action.kind.value, "error": "validation_rejected", "selector": action.target,
                }))
            else:
                try:
                    page.locator(action.target).click(timeout=manifest.timeout_ms)
                except PlaywrightError:
                    evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
                        "action_kind": action.kind.value, "error": "execution_failed", "selector": action.target,
                    }))
                else:
                    evidence.append(factory.create(EvidenceSource.ACTION, "runner", {
                        "action_kind": action.kind.value, "outcome": "executed", "selector": action.target,
                    }))
            # The browser context ends before a post-action assertion can be observed or persisted.
            context.close()
            context = None
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

    missing_assertion_evidence_id = "ev_{0}_{1:06d}".format(manifest.run_id, len(evidence) + 1)
    event = FailureEvent(
        failure_id="failure_f06", run_id=manifest.run_id, occurred_at=evidence[-1].captured_at,
        stage=FailureStage.ASSERTION, observed_facts={"status": "failed"},
        evidence_ids=tuple(record.evidence_id for record in evidence) + (missing_assertion_evidence_id,),
    )
    classification = DeterministicClassifier().classify(event, tuple(evidence))
    _write_artifacts(artifact_directory, evidence, event)
    (artifact_directory / "f06-results.json").write_text(
        canonical_json({
            "classification": {"applied_rule_id": classification.applied_rule_id,
                               "finding": classification.finding.to_dict()},
            "missing_assertion_evidence_id": missing_assertion_evidence_id,
        }),
        encoding="utf-8",
    )
    return F06EvaluationResult(
        failure_event=event, evidence=tuple(evidence),
        missing_assertion_evidence_id=missing_assertion_evidence_id,
        artifact_directory=artifact_directory, classification=classification,
    )
