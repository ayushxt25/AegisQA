# P0.4C.1 — Controlled Browser Evaluation Design

## Purpose and boundary

P0.4C will test the existing deterministic P0.4B classifier against controlled, local Playwright executions. It is an evaluation harness, not a product test framework, backend, LLM workflow, or classifier redesign. It must use Chromium through the existing Python Playwright dependency, run without a cloud service, and leave `AgentRunner`, `DeterministicClassifier`, and deterministic assertion semantics unchanged.

This document is a design checkpoint only. No browser scenario, fixture change, server, collector, or dependency is implemented by P0.4C.1.

Existing relevant behavior is deliberately reused:

- `experiment.py` already launches local Chromium, applies 10,000 ms defaults, captures a screenshot and trace, and closes context/browser in `finally` blocks.
- `PlaywrightBrowserTool.observe()` returns a policy-derived, bounded inventory and avoids sensitive fields; it does not make page content authorization.
- `AgentRunner` performs static and runtime action validation before click/fill and stops on a rejection.
- P0.4B contracts sanitize and digest `EvidenceRecord` data, while the classifier enforces R01–R06 and event-scoped evidence isolation.
- P0.4B.3 is synthetic only. Its answer key and metrics format are a pattern, not evidence that browser scenarios work.

## Local feasibility and Windows execution

The selected design requires only Python, Playwright Chromium, and the Python standard library. It is compatible with PowerShell because it uses `Path`, `127.0.0.1`, `socketserver`/`http.server`, and subprocess-free Python cleanup. P0.1 already established local-file Chromium feasibility, but this checkpoint does not claim that the P0.4C scenarios have run on this machine. The currently observed test environment lacks Playwright, so browser acceptance must run only after the documented install step.

Planned local prerequisite commands:

```powershell
Set-Location C:\Users\ayush\Documents\Projects\AegisQA
python -m pip install -r requirements.txt
python -m playwright install chromium
```

The existing `requirements.txt` pins Playwright 1.48.0 for Python 3.8. The Gemini dependency is conditional and is not used by P0.4C.

## Execution and evaluation manifests

Each scenario run will have an **execution manifest** created before Chromium starts. It may contain only scenario ID, fixture/server route, investigation `run_id`, fixture SHA-256, browser mode/version, viewport, timeout values, action policy, declared F05 condition/profile, and expected evidence shape. It contains neither an expected category nor expected confidence. For F05, the boundary profile has one shared investigation `run_id`, one group-level manifest, and ten unique `attempt_id` values; every boundary evidence record uses that shared `run_id`. The control profile has a separate investigation `run_id` and never contributes evidence to the boundary `FailureEvent`.

A separately stored **evaluation manifest** contains the F01–F06 answer key, acceptable classification/confidence, metric thresholds, and F05 expected profile. The evaluator may read it only after the collector has finalized evidence, built the `FailureEvent`, and called the classifier. The collector, classifier, browser runner, and their logs must not import, receive, or infer this manifest.

## Scenario matrix

| ID | Exact browser trigger | Required sanitized evidence / observations | `FailureEvent` and expected rule | Minimal fixture or local service change |
| --- | --- | --- | --- | --- |
| F01 invalid selector | Navigate the normal fixture, then submit a typed `click` for `#missing` while policy permits only `#increment`. Static validation rejects it before Playwright click. | Pre-action `OBSERVATION` with safe URL and permitted-element availability; `ACTION`/`runner` record with `error=validation_rejected`, `action_kind=click`, and selector `#missing`; no executed-click record. | `VALIDATION` event references both records. R02 may produce verified `AUTOMATION_FAILURE`. | None. |
| F02 disabled button | Navigate a disabled-button fixture, observe permitted `#increment` as available/visible but `enabled=false`, then submit the allowed click. Runtime validation rejects it before Playwright click. | Pre-action `OBSERVATION` with permitted `#increment`, `available=true`, `visible=true`, `enabled=false`; `ACTION`/`runner` `error=validation_rejected`; no executed-click record. | `VALIDATION` event. R02 may produce verified `AUTOMATION_FAILURE`. | Add one small controlled fixture that renders the existing button disabled. |
| F03 incorrect result | Navigate a faulty-result fixture. `#increment` is enabled and the approved click completes, but its handler intentionally leaves `#result` at `Clicks: 0`. The deterministic expected result remains `Clicks: 1`. | Pre/post safe observations, an `ACTION`/`runner` record with `outcome=executed`, and `EXECUTION_LOG`/`assertion` with `status=assertion_failed`. | `ASSERTION` event plus existing `TrustedAssertionContract` for the controlled expected result. R04 may produce verified `APPLICATION_DEFECT`. | Add a small fixture that keeps the result at zero after click. |
| F04 local HTTP 503 | Start a local stdlib server, navigate Chromium to its declared route, and record the returned response status 503. No external request is permitted. | `NETWORK`/`http_server` evidence with `http_status=503`, sanitized URL, timeout; optional environment evidence identifying only the local scenario/port. | `ENVIRONMENT` event plus existing `TrustedInfrastructureContext(SIMULATED_HTTP_503)`. R03 may produce verified `INFRASTRUCTURE_FAILURE`. | No HTML change required. Add a test-only standard-library 503 handler. |
| F05 intermittent outcomes | For each independent context in one shared boundary investigation run, navigate a delayed-result fixture, click enabled `#increment`, then wait for `Clicks: 1` using a fixed short assertion timeout. A deterministic boundary profile delays result update according to its declared trial schedule so some trials pass and others time out. | One `EXECUTION_LOG`/`assertion` record per uniquely identified trial (`assertion_passed` or `assertion_failed`); executed action evidence; safe observation facts including `injected_delay_ms`, timeout, target availability, and duration. All boundary records share the event `run_id` and have unique evidence IDs. | One boundary F05 event references only verified same-run evidence selected for the declared-equivalence group, plus two or more unique `RepeatedAttempt`s with one shared equivalence key. R05 yields evidence-supported `FLAKY_OR_INTERMITTENT`. | Add a controlled fixture whose result update delay is set only by the local scenario harness. |
| F06 ambiguous evidence | Run the normal fixture through an action/assertion boundary, then deliberately omit the decisive assertion or post-action record from finalized evidence while retaining a reference to it in the event. The omission is a collector-failure injection, not a fixture defect. | Existing safe pre-action/action record plus a required evidence ID absent from the supplied collection. Do not manufacture a result or raw error message. | `ASSERTION` event references the omitted ID. R01 returns `INSUFFICIENT_EVIDENCE`; no defect claim is permitted. | None beyond a harness-only evidence omission switch. |

The F01 and F02 scenarios intentionally exercise the existing validator rather than forcing a Playwright selector timeout. F06 intentionally validates evidence incompleteness, not browser correctness.

## Fixture layout and deterministic controls

The existing `fixture/index.html` remains the P0.1 success fixture and must not change its `Increment` / `Clicks: 1` behavior. P0.4C should add a small sibling scenario-fixture directory, for example:

```text
fixture/
  index.html                  # unchanged P0.1 baseline
  p0.4c/
    disabled-button.html      # F02
    incorrect-result.html     # F03
    delayed-result.html       # F05
```

Keeping scenario behavior in separate static files avoids query-string configuration: current `ActionPolicy` validation and safe observation URLs canonicalize local URLs, so a query parameter would be an unnecessary authorization/equality trap. Each fixture should contain only the fixed button/result markup needed for its scenario, no form values, credentials, remote assets, or arbitrary page text collection.

The P0.4C harness may set a non-secret test delay using an explicit local mechanism such as a DOM `data-delay-ms` attribute set before click. The collector records only the integer `injected_delay_ms`, never page script, HTML, or full text.

## Browser-to-evidence collection

The planned collector sits beside the P0.4C harness; it does not change `AgentRunner` or classifier code.

1. Create one `EvidenceIdFactory` per browser run before launch, using an injected UTC clock in tests.
2. Launch Chromium and a new browser context with fixed viewport, headless mode, default navigation/action timeout, and tracing enabled.
3. Before and after the scenario action, obtain `PlaywrightBrowserTool.observe()` data. Map—not copy—the observation into contract-safe facts: sanitized URL, boolean availability/visibility/enabled state, approved selector, tag/role where supported, and bounded permitted-element records. Do not pass `accessible_name`, title, raw `result`, or page labels into `EvidenceRecord` under the current contract because they are untrusted or not allowlisted.
4. Emit `ACTION` facts only from harness-controlled action state: safe action kind, approved target, `outcome=executed`, or safe `error=validation_rejected` / `timeout` / `execution_failed`. Never serialize exception text.
5. Emit assertion evidence from the existing deterministic boolean evaluator, using only `status=assertion_passed` or `status=assertion_failed`; the F03 expected result comes from a typed trusted contract, not page text.
6. For F04, emit `NETWORK` evidence only from the harness-owned response object and handler: safe URL, `http_status`, and bounded duration. The server response body is never stored.
7. Finalize every `EvidenceRecord`, verify all digests, then create a `FailureEvent` containing only the evidence IDs relevant to that event. Pass the event-scoped evidence collection, plus only the existing typed assertion/infrastructure/repeated-attempt metadata, to `DeterministicClassifier`.

`EvidenceRecord` currently accepts only its strict safe fact vocabulary. P0.4C must either map required facts into that vocabulary or propose a separately reviewed minimal contract extension before implementation. It must not weaken sanitization to capture browser labels, input values, HTML, cookies, headers, response bodies, query values, or exception messages.

## Classifier connection and evidence boundaries

The collector invokes `DeterministicClassifier.classify()` unchanged. It does not provide `FailureEvent.initial_category`, an evaluation label, a scenario name implying a category, or the evaluation manifest. The event contains all and only the records needed by the rule being evaluated. This preserves the P0.4B.2 repair: repeated attempts can use only digest-valid evidence explicitly named by their current event; conflicting duplicate IDs, cross-run records, missing records, and digest failures remain R01 inputs.

For F03 and F04, caller-convention trusted metadata is supplied only by the controlled harness after it verifies that it is executing that local scenario. This is enough for the current experimental interface but is not cryptographic authentication of scenario origin.

## F05 repeat protocol and reproducibility metrics

F05 has two declared conditions, each with ten fresh, independent browser contexts. Browser-context isolation does not assign a classifier run ID: `run_id` identifies an investigation/classification group, while `attempt_id` identifies a trial.

- **Control profile:** one separate control investigation `run_id`, fixed zero delay, and the same fixed assertion timeout. Its ten trials establish baseline environment behavior and remain outside the boundary classification group.
- **Boundary profile:** one shared boundary investigation `run_id`, the same fixture hash, Chromium version/channel, Playwright version, headless flag, viewport, action policy, navigation timeout, assertion timeout, and OS process. P0.4C.6 fixes the assertion timeout at 100 ms and the ten scheduled delays at `25, 300` ms repeated five times; every actual delay is recorded as `injected_delay_ms`. These parameters are declared before execution. The collector records observed outcomes and does not manufacture a mixed result or override the classifier.

Each trial has a new context/page and unique evidence IDs; no DOM state, local storage, or result counter is reused. The boundary group creates every `EvidenceRecord` through one group-owned `EvidenceIdFactory`, so all records retain the boundary `run_id` and receive monotonic unique IDs. The F05 `FailureEvent` uses that same boundary `run_id`, names only boundary evidence IDs, and every `RepeatedAttempt.evidence_id` must be one of those event IDs. A scenario-level equivalence key includes the fixture hash, browser/environment fields, policy version, timeouts, and declared boundary-profile identifier. It deliberately does **not** claim that individual raw delays are identical. This is a controlled intermittent outcome profile, not proof of naturally occurring flakiness. The execution manifest must state that equivalence is an experiment-specific declaration; P0.4C’s report must expose it.

Report these metrics separately for the two profiles:

- attempts started, completed, timed out, and cleanup failures;
- pass/fail counts and rate, plus the exact scheduled and observed delay values;
- median/minimum/maximum action and assertion durations;
- classifier category/confidence and R05 reproducibility status;
- fixture hash, runtime settings consistency, and evidence-integrity completeness rate.

Classification uses at least two verified, inconsistent boundary-profile attempts. The ten-trial protocol establishes repeatability of the injected profile; it does not elevate R05 beyond `EVIDENCE_SUPPORTED_INFERENCE`.

## F04 server and cleanup protocol

F04 uses `http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)`. Binding port `0` asks Windows for an available loopback port; the harness reads the assigned port from `server.server_address`, constructs the only allowed URL, and serves a fixed 503 status with a small non-sensitive response. The response body is neither logged nor classified.

The server runs in a daemon-capable, explicitly joined thread. Teardown must call `shutdown()`, join with a bounded timeout, then call `server_close()` even if Chromium launch/navigation fails. The browser context trace is stopped before context close, and context/browser close attempts occur in nested `finally` blocks. A teardown failure creates safe environment evidence but cannot overwrite the primary event or ground-truth result.

## Artifacts, privacy, integrity, and recovery

Per investigation run, write only under ignored `artifacts/p0.4c/<run-id>/`. For F05, store each fresh-context trial under `attempts/<attempt-id>/` beneath its group directory, while group-level evidence, event, and report remain at the shared run root. The control and boundary profiles use separate group directories:

```text
artifacts/p0.4c/
  <control-run-id>/attempts/<attempt-id>/
  <boundary-run-id>/
    attempts/<attempt-id>/
    evidence.json
    failure-event.json
    report.json
```

Each group directory contains only:

- screenshot and Playwright trace generated by the existing P0.1 pattern;
- sanitized finalized evidence JSON and a deterministic execution report;
- an artifact manifest with relative paths, size, and SHA-256 for each finalized file;
- no evaluation manifest, secrets, cookies, authorization data, request bodies, response bodies, page HTML, hidden/password/input values, or raw exception messages.

Artifact SHA-256 detects later modification relative to the recorded digest; it does not authenticate capture origin or prove correctness. A missing screenshot/trace is recorded as missing artifact evidence and lowers confidence; it never causes the harness to invent an artifact. A malformed event, digest failure, duplicate ID, or cleanup failure must be contained to that investigation run, use safe categorical errors, and leave subsequent scenarios with new contexts and new investigation run IDs.

Timeouts are explicit and bounded: launch, navigation, action/observation, assertion, trace stop, server shutdown, and thread join. The runner must use no unbounded retry. A scenario can be marked operationally failed while still yielding an R01 insufficient-evidence finding; the evaluation manifest must distinguish harness availability from classifier correctness.

## Acceptance criteria and test strategy

P0.4C implementation is acceptable only when all of the following are demonstrated locally:

1. F01–F06 each execute with local Chromium or the stdlib loopback server only; no remote request, Gemini call, or new service occurs.
2. Every finalized record is sanitized, run-scoped, sequentially identified, digest-valid, and present only in its own event unless an adversarial test deliberately violates that condition.
3. F01/F02 reject before click; F03 has verified executed-action and failed-assertion evidence plus the trusted contract; F04 has 503 evidence plus typed injected-condition context; F05 has ten trials per declared condition, one shared boundary investigation `run_id`, unique attempt/evidence IDs, and at least two independent inconsistent boundary attempts named by the boundary event; F06 lacks decisive evidence by design.
4. The existing classifier source and R01–R06 behavior are unchanged. The classifier never receives the evaluation manifest or expected label.
5. Reports are deterministic for equivalent collected evidence, generated artifacts remain ignored, and cleanup completes for browser/server success and failure paths.
6. Focused tests cover fixture behavior, evidence mapping/sanitization, event membership/digest failures, local 503 lifecycle, F05 counting/equivalence metadata, manifest isolation, artifact path containment, timeout/cleanup handling, and metrics calculation.
7. A PowerShell verification run reports actual results and skips neither Playwright tests nor browser installation errors. Any unavailable Chromium/runtime condition is reported as an environment blocker, not a passing evaluation.

Planned verification commands after implementation:

```powershell
Set-Location C:\Users\ayush\Documents\Projects\AegisQA
python -m unittest discover -s tests -v
python -m failure_intelligence.browser_evaluation --output artifacts/p0.4c/evaluation.json
Get-Content artifacts/p0.4c/evaluation.json
```

## Proposed implementation sequence

1. Add execution/evaluation manifest dataclasses or files with strict separation and tests that classifier call paths cannot access answer-key fields.
2. Add the three minimal static fixtures and fixture-only tests; keep `fixture/index.html` unchanged.
3. Build a local browser collector that maps existing observations/actions/assertions to current sanitized contracts and writes bounded artifacts.
4. Add the loopback 503 context manager and F04 browser test with reliable shutdown.
5. Add F01–F03/F06 execution cases, then the fresh-context F05 ten-trial protocol and reproducibility aggregation.
6. Join finalized classifier outputs to the evaluation manifest only in the reporting layer; test deterministic output and adversarial integrity cases.
7. Execute the PowerShell verification commands on a machine with Chromium installed and record actual, not projected, metrics.

## Unresolved risks and limitations

- Current contract fields omit raw assertion result/page text and accessible names by design. The collector must prove it can derive all required rule facts without a contract extension; any extension needs separate review.
- `TrustedAssertionContract` and `TrustedInfrastructureContext` remain trusted by caller convention, not authenticated provenance.
- The F05 boundary profile proves a deliberately injected mixed outcome pattern, not a production flake or general browser nondeterminism.
- Screenshot and trace files can contain sensitive page content even when evidence JSON is sanitized; this experiment is restricted to controlled local fixtures and ignored local artifacts.
- Windows port allocation, antivirus/browser policy, Chromium installation, and trace finalization can fail operationally. These must be measured and reported, not hidden by retries.
- No conclusion about real application diagnosis, autonomous remediation, or LLM investigation follows from P0.4C.
