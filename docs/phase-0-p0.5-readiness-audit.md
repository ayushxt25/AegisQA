# P0.5 — Phase 0 Architecture and Readiness Audit

## Scope, method, and status language

This documentation-only audit reviews the repository, tests, Phase 0 records, and chronological engineering history as they exist at the audit checkpoint. It does not change implementation or claim production readiness.

- **Implemented** means source and focused tests are present in this repository.
- **Verified externally** means the result is recorded as a completed user Windows/Playwright Chromium run, not rerun in the current Codex environment.
- **Designed** means documented intent without a corresponding implementation.
- **Missing** means neither sufficient design nor implementation is present for a Phase 1 product concern.

## Executive assessment

**Recommendation: CONDITIONAL GO for Phase 1 architecture and foundation work; NO-GO for any public, multi-user, or production browser-testing service.**

Phase 0 has achieved its intended feasibility objective. It proves a bounded local Chromium execution path, typed action validation, a provider-independent planner boundary, optional bounded Gemini planning, deterministic assertions, security-conscious observations, evidence contracts, deterministic failure classification, and controlled F01–F06 evaluation. The user-recorded Windows run verifies six real local browser scenarios with the expected controlled classifications and F05 profile.

It deliberately does not contain a product definition, backend/API, persistence layer, authentication, tenancy model, browser worker isolation, artifact-storage policy, deployment topology, or operational recovery model. These are not defects in Phase 0; they are the intended boundary. They are nevertheless Phase 1 design gates because moving browser execution from a local fixture to user-controlled targets changes the security and reliability posture materially.

The smallest responsible next step is to resolve the P0 design decisions below, then begin a narrow Phase 1 foundation slice: authenticated project/execution ownership, a persistent job model, and a deny-by-default worker security model. Do not expose arbitrary URL navigation, credentials, shared artifacts, or LLM-driven browser execution before those decisions have implementation acceptance criteria.

## Verified Phase 0 achievements

| Area | Status | Evidence |
| --- | --- | --- |
| Local Chromium interaction, screenshot, trace, duration, and cleanup | Implemented; verified externally | `experiment.py`; `tests/test_experiment.py`; `docs/phase-0-p0.1-browser-runtime-feasibility.md` |
| Bounded stateful browser-agent loop | Implemented | `agent_runtime/runner.py`, `state.py`, `actions.py`; `tests/test_agent_runtime.py` |
| Static and runtime action authorization | Implemented | `validate_action()` in `agent_runtime/actions.py`; `validate_runtime_action()` in `agent_runtime/runner.py` |
| Policy-derived structured observations | Implemented | `agent_runtime/browser.py`; `tests/test_browser_observations.py` |
| Optional, bounded Gemini structured planner | Implemented for a controlled experiment | `agent_runtime/gemini_planner.py`; `tests/test_gemini_planner.py`; `docs/phase-0-p0.3b-gemini-planner.md` |
| Typed sanitized evidence and integrity checking | Implemented | `failure_intelligence/contracts.py`; `tests/test_failure_intelligence.py` |
| Deterministic failure classification and synthetic evaluation | Implemented | `failure_intelligence/classifier.py`, `synthetic_evaluation.py`; `tests/test_failure_intelligence.py` |
| Controlled F01–F06 browser harness | Implemented; verified externally in Windows/Chromium | `failure_intelligence/browser_collector.py`, `browser_evaluation.py`; `tests/test_browser_collector.py`, `tests/test_browser_evaluation.py`; `docs/phase-0-p0.4c-browser-evaluation-design.md` |

The P0.4C.8 record states that the user Windows environment completed 75/75 tests with no skips, executed all six real browser scenarios, achieved 6/6 category matches and 6/6 confidence matches, produced zero false application-defect classifications, and measured F05 control at 10 pass / 0 fail and boundary at 5 pass / 5 fail. Those are valid **controlled local experiment** results only.

The current Codex environment is materially different: `python -m unittest discover -s tests -v` completed 78 tests with 12 skips because Playwright/Chromium and loopback sockets are unavailable here. This audit does not replace or contradict the recorded Windows verification.

## 1. Product definition audit

| Concern | Status | Assessment |
| --- | --- | --- |
| Problem statement | Partially designed | The evidence-first failure-intelligence problem is clear in `docs/phase-0-p0.4-failure-intelligence-design.md`; the end-user problem is not defined. |
| Target users and roles | Missing | No personas, buyer, operator, QA engineer, developer, or administrator responsibilities are defined. |
| Core requirements | Partially designed | Phase 0 requirements are strong and testable; product-level functional and non-functional requirements are absent. |
| Non-goals | Implemented as Phase 0 boundaries | The records consistently exclude backend, database, frontend, production autonomy, and cloud deployment. |
| Competitive differentiation | Missing | No comparison, positioning, or measurable product advantage is stated. |
| MVP boundary | Missing | There is no definition of the first customer workflow, supported application type, test-authoring model, or success metric. |

**Finding:** Phase 1 must not infer a product from the local fixture. A concise product brief is a P0 decision because it determines the security model, data model, and worker boundary.

## 2. System architecture audit

### Implemented local architecture

```text
Planner (mock by default; optional Gemini)
  -> BrowserAction
  -> static ActionPolicy validation
  -> latest policy-derived observation feasibility validation
  -> PlaywrightBrowserTool
  -> Observation + deterministic assertion
  -> ExecutionState / structured local log

Controlled collector
  -> sanitized EvidenceRecord(s) + FailureEvent
  -> DeterministicClassifier (R01–R06)
  -> evaluation-only ground-truth comparison
```

Responsibilities are suitably separated for a feasibility experiment:

- `agent_runtime/planner.py` defines the provider-independent planner protocol and deterministic mock implementation.
- `agent_runtime/runner.py` owns the bounded observe → propose → validate → execute → observe loop and terminal state semantics.
- `agent_runtime/browser.py` confines page operations and returns policy-derived observations.
- `failure_intelligence/contracts.py` owns sanitized, canonical, digestable evidence shapes.
- `failure_intelligence/classifier.py` owns deterministic classification only; it rejects missing, contradictory, cross-run, and invalid evidence conservatively.
- `failure_intelligence/browser_collector.py` and `browser_evaluation.py` execute controlled scenarios and join answer-key data only after classification.

### Architecture gaps

| Concern | Status | Phase 1 implication |
| --- | --- | --- |
| Request/API boundary | Missing | No authentication context, request validation contract, versioning, or error model exists. |
| Domain model | Missing | The local `ExecutionState` is not a durable product execution/job model. |
| Project/user isolation | Missing | Action policies, evidence, artifacts, and run IDs are local and not tenant-scoped. |
| Orchestration boundary | Missing | There is no service-to-worker protocol, queue, cancellation mechanism, or recovery owner. |
| Browser isolation | Missing | Local Playwright runs in-process; it is not a hostile-target isolation boundary. |
| LLM investigation | Designed, not implemented | P0.4D remains explicitly deferred; no LLM should alter deterministic findings. |

## 3. Backend and persistence audit

**Implemented:** none. There is no API server, database client, migration tooling, PostgreSQL schema, repository layer, worker queue, or durable artifact catalog. This matches the stated Phase 0 restriction.

**Designed:** P0.4 contracts offer useful starting shapes (`EvidenceRecord`, `FailureEvent`, `InvestigationFinding`, and `InvestigationReport` in `failure_intelligence/contracts.py`). They are not a PostgreSQL schema and have no foreign keys, retention policy, access controls, version migration, or transaction boundaries.

**Required before a Phase 1 implementation slice:**

1. Define ownership keys and lifecycle entities: organization, user/service principal, project, target, test definition, execution, attempt, artifact, evidence, finding, and evaluation result.
2. Define a durable execution/job state machine separate from `ExecutionState`: queued, claimed, starting, running, cancelling, cancelled, succeeded, failed, timed_out, and recovery-required. Define legal transitions, cancellation, lease expiry, and terminal immutability.
3. Define idempotency keys for execution creation, worker claim/heartbeat semantics, retry eligibility, attempt lineage, and crash recovery. A retry must create a distinct attempt and never overwrite evidence.
4. Decide whether PostgreSQL is the system of record from the first Phase 1 slice, and write migration, backup, deletion, and retention requirements before implementing tables.

## 4. Security audit

### Implemented controls and their limits

| Control | Evidence | Limit |
| --- | --- | --- |
| Static allowlists for URLs/selectors | `agent_runtime/actions.py` | Fixture-specific policy; no target onboarding or tenant-aware authorization. |
| Runtime availability/visibility/enabled checks | `agent_runtime/runner.py` | Protects a stale local observation path; does not sandbox page behavior. |
| URL query/credential stripping in observations/evidence | `agent_runtime/browser.py`; `failure_intelligence/contracts.py` | Does not control outbound navigation or prevent a page from accessing internal resources. |
| Bounded, allowlisted evidence; raw input values and known secret fields excluded | `failure_intelligence/contracts.py` | Screenshot/trace artifacts may still contain sensitive visual content. |
| Gemini key via environment and bounded requests | `agent_runtime/gemini_planner.py` | No secret manager, rotation, per-project scoping, audit trail, or provider policy. |
| Prompt-injection guidance | `GeminiPlanner._build_context()` and P0.4 design | No hostile-content benchmark or P0.4D resistance implementation. |

### Missing security requirements

- Authentication, session/token validation, authorization, RBAC, and audit logging.
- Organization/project-level row and artifact isolation.
- SSRF defenses: target eligibility policy, DNS/IP resolution controls, redirect checks, private-network and metadata-service blocks, and egress enforcement.
- Browser worker containment: dedicated identity, sandbox/container/VM decision, network policy, CPU/memory/process ceilings, filesystem isolation, download/upload policy, and browser patching process.
- Credential model: encrypted storage or ephemeral injection, redaction coverage, rotation, least privilege, and prevention of credentials entering traces/screenshots/logs.
- Prompt-injection test suite for hostile page text, browser errors, traces, and model output before any LLM investigation feature.

**Security conclusion:** the local action policy is valuable defense in depth, but it is not sufficient authorization or isolation for user-supplied targets. This is a Phase 1 P0 blocker for any remote-target execution.

## 5. Deployment feasibility audit

| Topic | Status | Audit finding |
| --- | --- | --- |
| Local-first execution | Implemented | Windows PowerShell commands and local Chromium evidence are documented. |
| Frontend/backend hosting | Missing | No application services exist. |
| PostgreSQL hosting | Missing | No service choice, schema, backup, connection policy, or cost model. |
| Browser worker hosting | Missing | Local success does not establish container, VM, managed-worker, concurrency, or resource feasibility. |
| Artifact storage | Partially designed | Artifacts are ignored local files; no object-store design, encryption, retention, access policy, or deletion workflow exists. |
| Free-tier limits/costs | Missing | Gemini costs are qualitatively bounded, but hosting, database, storage, egress, and browser-worker costs are not modeled. |
| Operational observability | Missing | No metrics, tracing, alerting, SLOs, runbook, or incident/recovery process exists. |

The smallest deployment decision for Phase 1 is not a cloud deployment. It is an ADR that compares a local-first developer mode with one isolated worker deployment option, explicitly recording expected concurrency, artifact volume, data residency, free-tier constraints, and a shutdown/fallback path.

## 6. Testing and evaluation audit

### Existing coverage

- `tests/test_agent_runtime.py` exercises loop success/failure, invalid actions, action budget, state transitions, and runtime feasibility rejection.
- `tests/test_browser_observations.py` exercises observation limits, sensitive-value exclusion, missing elements, and enabled/visible state.
- `tests/test_gemini_planner.py` uses mocked client behavior for output, provider-error, and call-limit paths; it does not perform provider calls.
- `tests/test_failure_intelligence.py` exercises deterministic serialization/sanitization, digest tampering, classification R01–R06, adversarial evidence, and synthetic metrics.
- `tests/test_manifests.py`, `test_browser_collector.py`, `test_browser_evaluation.py`, and `test_p0_4c_fixtures.py` exercise manifest separation, controlled browser collection/evaluation, partial-result handling, and F05 identity rules.

### Strengths

- The classifier is appropriately conservative: missing/corrupt/cross-run evidence and contradictions resolve to insufficient evidence rather than a defect claim.
- The Windows P0.4C.8 result is a meaningful controlled end-to-end baseline, including real browser F01–F06 execution.
- The current local test run passes all available tests: 78 run, 12 skipped because this environment lacks the browser prerequisites. Skips are not treated as verification.

### Remaining benchmark requirements

- Repeat the controlled browser evaluation in a pinned CI browser image and retain a versioned report; one Windows run is not a regression program.
- Add browser/worker resource, timeout, crash, cleanup, cancellation, and concurrent-execution benchmarks.
- Add representative application targets only after SSRF and credential controls exist; keep their ground truth and sensitive data isolated.
- Add P0.4D evidence-grounding and prompt-injection resistance benchmarks before an LLM writes investigation content for users.
- Define quality metrics for Phase 1 beyond scenario accuracy: execution success rate, false defect rate, evidence completeness, queue delay, latency, artifact size, and recovery success.

## 7. Engineering documentation audit

### Strengths

- The P0.1–P0.4 records preserve objectives, constraints, verification commands, known limitations, and key debugging history.
- `docs/phase-0-p0.4-failure-intelligence-design.md` contains a chronological engineering log and detailed evidence/classification rationale.
- Controlled Windows browser results are now explicitly distinguished from the unavailable Codex browser environment.

### Documentation contradictions and debt

| Priority | Finding | Evidence | Required correction |
| --- | --- | --- |
| P1 | README still says there is no AI agent, although the repository contains `agent_runtime/` and an optional Gemini planner. | `README.md`; `agent_runtime/` | Reframe README as the current Phase 0 experiment inventory and preserve its non-production boundary. |
| P1 | The P0.4 design introduction and several early sections retain point-in-time wording that says evidence contracts/classification do not exist, although they are implemented. | `docs/phase-0-p0.4-failure-intelligence-design.md`; `failure_intelligence/` | Mark historical design statements as superseded or add an implemented-status section. |
| P1 | The P0.4C design shows `browser_evaluation --output ...`, but the implemented CLI accepts `--run-id`. | `docs/phase-0-p0.4c-browser-evaluation-design.md`; `failure_intelligence/browser_evaluation.py` | Correct the verification command. |
| P1 | P0.4C design calls for screenshot/trace and artifact-manifest hashing, but the collector writes sanitized evidence/event/result JSON only. | `docs/phase-0-p0.4c-browser-evaluation-design.md`; `failure_intelligence/browser_collector.py` | Either implement those artifacts in a later scoped task or document them as deferred. |
| P2 | Python guidance is inconsistent: docs/requirements say the optional SDK needs Python 3.9+, while one planner error message says Python 3.11. | `requirements.txt`; `docs/phase-0-p0.3b-gemini-planner.md`; `agent_runtime/gemini_planner.py` | Select and document one supported optional-Gemini version floor. |
| P1 | No ADR directory or decisions exists for the Phase 1 choices below. | Repository file inventory | Create concise ADRs before implementation commits depend on those choices. |

## Prioritized gaps and smallest closure tasks

| Priority | Gap | Smallest necessary closure task | Why it is a gate |
| --- | --- | --- | --- |
| P0 | Product scope absent | Write a one-page product/MVP brief: target user, first workflow, supported targets, explicit non-goals, success metrics, and data sensitivity. | Prevents building the wrong API, tenant model, and browser policy. |
| P0 | No threat model for remote execution | Write and approve an ADR/threat model for authentication, tenant isolation, target onboarding, SSRF/egress, browser-worker containment, credentials, artifacts, and LLM data flow. | Do not execute user-controlled URLs or credentials without it. |
| P0 | No durable execution model | Write ADRs for API boundary, PostgreSQL ownership/schema outline, execution/attempt state machine, idempotency, lease/retry/recovery semantics, and artifact ownership. | Prevents irreversible incompatible Phase 1 persistence and worker choices. |
| P0 | No deployability decision | Select a local-first development mode and one bounded worker/deployment topology with cost/resource assumptions and artifact retention. | Browser workers and storage cannot be safely assumed to fit an arbitrary free tier. |
| P1 | Documentation drift | Reconcile README, P0.4 implementation status, CLI commands, artifact scope, and Python version guidance. | Keeps implementation decisions reviewable and reproducible. |
| P1 | No persistent application slice | Implement authenticated project/execution creation, PostgreSQL migrations, durable job/attempt records, and a non-public worker boundary after P0 ADR approval. | First Phase 1 vertical slice. |
| P1 | Worker isolation absent | Implement deny-by-default target/network policy, resource limits, cancellation, and safe artifact handling before non-fixture targets. | Core safety and reliability requirement. |
| P1 | Evaluation automation incomplete | Add pinned-browser CI, report retention, and real-worker failure/recovery/concurrency tests. | Converts a controlled local proof into a repeatable engineering signal. |
| P2 | LLM investigation | Implement P0.4D only after deterministic and isolation baselines; include hostile prompt-injection evaluation. | Not required for the first Phase 1 foundation. |
| P2 | Broader product capability | Add cross-browser coverage, integrations, dashboards, scheduling, and autonomous replanning only after the core boundaries are proven. | Avoids premature scope expansion. |

## Phase 1 entry criteria

Phase 1 may start when the following P0 decisions are accepted and recorded, not merely discussed:

1. Product/MVP brief names the supported user, first workflow, target type, data classification, and non-goals.
2. Security/threat-model ADR defines the remote-target decision. Until it does, Phase 1 remains local-fixture or explicitly controlled-target only.
3. Persistence/API/job-state ADR defines identity ownership, PostgreSQL entities, execution-versus-attempt semantics, idempotency, cancellation, retry, and recovery.
4. Deployment ADR defines local development, worker isolation, artifact storage/retention, observability, cost guardrails, and fallback/shutdown behavior.
5. Documentation corrections above are made, including a current verification command and an explicit artifact-scope statement.
6. A pinned Windows or CI Chromium verification path is selected for ongoing F01–F06 regression evidence; this does not need to block the documentation/ADR work itself.

## Recommendation and implementation sequence

**Conditional GO.** Begin Phase 1 with the four P0 design tasks, then implement only the first secure vertical slice. Do not begin frontend dashboards, public API exposure, broad target support, production Gemini investigation, or autonomous retries in that slice.

Recommended sequence:

1. Product/MVP brief and ADRs for security, persistence/job lifecycle, and deployment.
2. Define API contracts and PostgreSQL migration plan from the approved ADRs; add tenant ownership to every durable record.
3. Implement a local-first authenticated execution-creation path with no arbitrary remote targets.
4. Add an isolated worker and artifact boundary with cancellation/lease/recovery tests.
5. Expand target onboarding only after SSRF, credential, and network controls are tested.
6. Keep P0.4 deterministic evidence/classification as a reusable subsystem; defer P0.4D and broader LLM behavior.

This preserves Phase 0's proven work instead of redesigning it. Its local action/evidence abstractions are useful inputs to Phase 1, but must sit behind durable identity, authorization, and worker-isolation boundaries.
