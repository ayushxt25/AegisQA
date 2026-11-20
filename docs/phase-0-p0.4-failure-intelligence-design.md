# Phase 0 / P0.4A — Failure Intelligence Design & Evaluation Contract

## Status and scope

This is a design-only feasibility contract. It does not add failure collection, classification, scenario injection, LLM investigation, or changes to existing execution code.

The inspected implementation already provides: Playwright execution; typed `navigate`, `click`, `fill`, `inspect`, and `finish` actions; static `ActionPolicy` validation; state-aware feasibility checks before click/fill; bounded policy-derived browser observations; a deterministic `Clicks: 1` assertion; a bounded Gemini structured-output planner; and a JSON execution log containing execution state and available inference metadata. It does **not** yet provide typed P0.4 evidence records, failure categories, integrity checks, reproducibility analysis, or an investigation report.

P0.4's problem is to turn a failed bounded execution into an evidence-grounded, reviewable statement without treating a model narrative as fact. Its scope is controlled local experiments first. It is not root-cause proof for arbitrary applications, a production incident system, or an autonomous remediation loop.

## Failure and confidence taxonomy

| Category | Deterministic meaning | Example |
| --- | --- | --- |
| `APPLICATION_DEFECT` | In a controlled scenario, application behavior contradicts independently established expected behavior while runner, action, and environment evidence are complete. In a real application, an assertion failure alone is not proof of an application defect. | A click succeeds but the page reports `Clicks: 0` when the controlled scenario independently establishes `Clicks: 1`. |
| `AUTOMATION_FAILURE` | The test/agent/selector/validation path failed to perform or verify the intended interaction. | An invalid selector is rejected or a target is unavailable. |
| `INFRASTRUCTURE_FAILURE` | A dependency outside intended application behavior prevents execution. | A simulated HTTP 503 or browser/provider service failure. |
| `FLAKY_OR_INTERMITTENT` | Repeated controlled attempts produce inconsistent outcomes under equivalent declared conditions. | Delayed rendering crosses a timeout boundary on some attempts only. |
| `INSUFFICIENT_EVIDENCE` | Available evidence cannot distinguish categories safely. | A failed assertion with no reliable action outcome or observation. |

| Confidence | Meaning | Permitted claim |
| --- | --- | --- |
| `VERIFIED` | A deterministic rule and intact evidence directly establish the finding. | "The runner rejected `#missing` before browser execution." |
| `EVIDENCE_SUPPORTED_INFERENCE` | Multiple evidence items support a likely category, but an alternative remains. | "The 503 likely prevented application loading." |
| `HYPOTHESIS` | A plausible explanation lacking adequate corroboration. | "Rendering timing may have caused the failure." |

No finding may be more confident than its weakest indispensable evidence item. `VERIFIED` must name its deterministic rule and evidence IDs. `APPLICATION_DEFECT` with `VERIFIED` is permitted only for a controlled scenario with independently established expected behavior and complete supporting action, observation, assertion, and environment evidence; real-application assertion failure alone is at most evidence requiring further investigation.

## Proposed typed contracts

The following are proposed Python dataclass/enum contracts for P0.4B; no contract exists in the current code.

```text
EvidenceRecord
  evidence_id: str                 # stable: ev_<run-id>_<sequence>
  run_id: str
  source: EvidenceSource           # execution_log | observation | action | screenshot | trace | network | environment
  captured_at: RFC3339 UTC timestamp
  observed_facts: Mapping[str, JSON-safe value]
  artifact_reference: optional relative artifact path
  content_digest: optional SHA-256 digest
  sanitization_status: SanitizationStatus

FailureEvent
  failure_id: str                  # stable: fail_<run-id>_<sequence>
  run_id: str
  occurred_at: RFC3339 UTC timestamp
  stage: FailureStage              # planning | validation | execution | assertion | environment
  observed_facts: Mapping[str, JSON-safe value]
  evidence_ids: Sequence[str]
  initial_category: FailureCategory or null

InvestigationFinding
  finding_id: str                  # stable: find_<report-id>_<sequence>
  classification: FailureCategory
  confidence: ConfidenceLevel
  observed_facts: Sequence[str]
  supporting_evidence_ids: Sequence[str]
  contradictory_evidence_ids: Sequence[str]
  uncertainty: str
  missing_evidence: Sequence[str]
  reproducibility: ReproducibilityStatus

InvestigationReport
  report_id: str                   # stable: report_<run-id>
  run_id: str
  created_at: RFC3339 UTC timestamp
  contract_version: str
  failure_event_ids: Sequence[str]
  findings: Sequence[InvestigationFinding]
  deterministic_summary: str
  llm_summary: optional str
  overall_confidence: ConfidenceLevel
  unresolved_questions: Sequence[str]
```

`ReproducibilityStatus` should initially be `NOT_ATTEMPTED`, `REPRODUCED`, `NOT_REPRODUCED`, or `INCONCLUSIVE`. `EvidenceSource`, `FailureStage`, `FailureCategory`, `ConfidenceLevel`, and `SanitizationStatus` should be closed enums. Stable IDs are assigned by the collector, never supplied by an LLM.

### Manifest separation

P0.4 uses two separate versioned inputs:

- An **execution manifest** is accessible to the collector and classifier. It declares scenario setup, permitted actions, expected evidence shape, timeouts, injected parameters, and run metadata. It contains no ground-truth category or expected classification.
- An **evaluation manifest** is accessible only to the evaluation harness after report generation. It contains controlled-scenario ground truth, acceptable classifications, and metric expectations.

The classifier must never receive, load, or infer labels from the evaluation manifest. This separation applies to deterministic rules and any future LLM packet alike.

## Evidence provenance and integrity

- One run receives a generated `run_id` before browser launch; all P0.4 IDs derive from it and a monotonic sequence.
- Record capture timestamps in UTC and preserve the producing component (`runner`, `browser`, `assertion`, or scenario harness).
- Store immutable, relative references to screenshot, trace, and execution-log artifacts; calculate SHA-256 only for finalized sanitized evidence files before report generation.
- Preserve evidence files only in the local ignored artifact directory. A report references digests and safe summaries, not artifact contents.
- SHA-256 detects modification relative to a trusted previously recorded digest. It does not establish evidence authenticity, source identity, completeness, or correctness at capture time.
- Never upgrade an observation into ground truth. Ground truth belongs only to the controlled scenario declaration.
- If an artifact is absent, unreadable, modified after digest, or lacks a source/timestamp, mark it unavailable and lower confidence rather than reconstructing it.

## Controlled P0.4 evaluation scenarios

| ID | Setup and injected failure | Expected observations | Known ground truth | Acceptable classification | Misleading conclusion to avoid |
| --- | --- | --- | --- | --- | --- |
| F01 invalid selector | Planner/test proposes a selector outside `ActionPolicy`. | Static validation rejection; no browser action record; no Playwright timeout. | Selector is invalid for this experiment. | `AUTOMATION_FAILURE`, `VERIFIED`. | Calling it an application defect because a click did not occur. |
| F02 disabled button | Fixture exposes policy-approved `#increment` with `available=true`, `visible=true`, `enabled=false`. | Runtime feasibility rejection before execution; latest observation names disabled state. | Interaction is intentionally unavailable. | `AUTOMATION_FAILURE`, `VERIFIED`. | Claiming the app failed its assertion; no interaction was performed. |
| F03 incorrect application result | Click is feasible and completes, but controlled fixture keeps result at `Clicks: 0`. | Executed click record, post-action observation, deterministic assertion false. | Fixture intentionally violates expected result. | `APPLICATION_DEFECT`, `VERIFIED` in the controlled fixture. | Blaming the planner or selector despite successful action evidence. |
| F04 simulated HTTP 503 | A Python standard-library HTTP server bound to `127.0.0.1` on an OS-assigned port returns 503 for the declared route. | Navigation error or captured 503 status evidence. | Local dependency outage is injected. | `INFRASTRUCTURE_FAILURE`, `VERIFIED` only with captured 503 evidence; otherwise `EVIDENCE_SUPPORTED_INFERENCE`. | Treating absence of a target as a selector defect. |
| F05 intermittent rendering delay | Fixture records injected delay and runs ten controlled attempts for each declared condition using a fixed timeout. Boundary-profile attempts share one investigation `run_id` with the F05 event but retain unique attempt/evidence IDs; control-profile evidence is a separate run. Equivalent attempts share fixture version, browser/runtime settings, action policy, timeout, and declared delay condition. | Mixed attempt outcomes, timings, injected delays, and target availability observations. | Delay pattern is injected. | `FLAKY_OR_INTERMITTENT`, `EVIDENCE_SUPPORTED_INFERENCE` after the ten-attempt protocol. | Calling one timeout proof of flakiness. |
| F06 ambiguous failure | Remove or corrupt the decisive post-action observation while retaining an assertion failure. | Assertion false with missing/contradictory action or observation evidence. | Evidence is deliberately insufficient. | `INSUFFICIENT_EVIDENCE`, `VERIFIED`. | Guessing an application, automation, or infrastructure root cause. |

## Deterministic classification rules

P0.4B should apply these ordered rules before any LLM call:

1. Missing required evidence, failed integrity check, or irreconcilable evidence yields `INSUFFICIENT_EVIDENCE`.
2. A static or runtime validation rejection yields `AUTOMATION_FAILURE`; the rule ID and rejected condition must be recorded.
3. A permitted-producer HTTP 5xx verifies an observed service failure, not its infrastructure root cause. `INFRASTRUCTURE_FAILURE` is `VERIFIED` only when additional trusted controlled-scenario injected-condition context establishes the cause; a generic action timeout alone is not sufficient.
4. A recorded successful action followed by a deterministic assertion contradiction yields `APPLICATION_DEFECT` only in a controlled scenario whose independently established expected behavior and complete supporting evidence are available to the evaluator. In real applications, assertion failure alone does not prove an application defect.
5. A declared repeated-trial protocol with both success and failure under equivalent recorded conditions yields `FLAKY_OR_INTERMITTENT`; otherwise use `INSUFFICIENT_EVIDENCE` or an inference.
6. If no rule applies, emit `INSUFFICIENT_EVIDENCE`, not a default defect label.

Rule precedence is strict: R01 evidence integrity, run isolation, and unresolved contradictions; R02 validation rejection; R03 observed service failure; R04 trusted assertion contradiction after a completed action; R05 equivalent inconsistent repeated attempts; then R06 insufficient evidence. `FailureEvent.initial_category` is descriptive input only and is never used as proof. `TrustedAssertionContract` and controlled injected-condition context are trusted by caller convention at this checkpoint; the classifier does not independently authenticate their origin.

## Deterministic analysis versus LLM investigation

Deterministic code owns artifact discovery, sanitization, digest verification, rule execution, stable IDs, category eligibility, confidence ceilings, and report serialization. An LLM may only receive a sanitized evidence packet and may summarize, identify contradictions, enumerate alternative hypotheses, and propose missing evidence. It may not assign IDs, alter evidence, raise confidence, change a deterministic classification, execute a browser action, or declare ground truth.

An LLM-generated statement is at most `EVIDENCE_SUPPORTED_INFERENCE` unless a deterministic rule independently verifies it. The P0.4B baseline should run with no LLM at all.

## Sanitization, privacy, and prompt-injection safeguards

- Collect only allowlisted sanitized fields. Exclude API keys, secrets, cookies, authorization headers, sensitive query strings, request bodies, input values, password/hidden fields, and full-page HTML from evidence packets.
- Bound strings, lists, stack traces, response bodies, and artifact excerpts. Persist safe summaries plus digest/reference, not raw secrets.
- Treat all page text, labels, error messages, trace content, and provider output as untrusted data. Delimit evidence as data in any future prompt and state that it cannot alter instructions, permissions, classification rules, or tool use.
- Never follow instructions embedded in a page, log, response body, selector label, screenshot OCR, or trace.
- Maintain an allowlist of evidence fields sent to an LLM; omit raw URLs beyond the existing sanitized form.

## Evaluation metrics and baselines

The baseline is deterministic rules over the same sanitized evidence, with no LLM. Measure each controlled scenario over a declared number of attempts:

- category accuracy against scenario ground truth;
- confidence calibration: `VERIFIED` only when the designated deterministic evidence is complete;
- insufficient-evidence precision: F06 must not be over-classified;
- false application-defect rate for F01, F02, and F04;
- reproducibility decision accuracy for F05;
- evidence completeness rate (required evidence records present and digest-valid);
- report determinism: equivalent evidence produces byte-equivalent normalized deterministic sections;
- investigation latency, artifact size, and optional LLM token/cost metadata.

LLM evaluation compares its sanitized-report quality against the deterministic baseline; it is not credited for matching labels when the required evidence is absent.

### P0.4B.3 synthetic evaluation checkpoint

The P0.4B.3 harness builds F01–F06 as deterministic typed execution inputs and passes only those inputs to the classifier. A private evaluation-only answer key is joined only after classification to calculate category accuracy, confidence-level agreement, false-application-defect count, and insufficient-evidence handling. Its normalized JSON output is written only below the ignored local `artifacts/` directory.

F03 supplies the existing caller-convention `TrustedAssertionContract`; raw assertion text is never used as expected behavior. F04 uses synthetic `NETWORK`/`http_server` 503 evidence and the existing typed `TrustedInfrastructureContext`, with no server or request. F05 provides two independent assertion records with a shared declared equivalence key. That equivalence is assumed for this synthetic checkpoint, not experimentally established; the ten-attempt protocol remains P0.4C work. F06 omits its referenced evidence and must remain insufficient evidence.

Adversarial synthetic cases cover a corrupt digest, missing reference, contradictory action records, contradictory assertion outcomes, cross-run evidence, and conflicting duplicate evidence IDs. They must never produce a `VERIFIED` application-defect finding. The first focused run exposed only a test-class placement mistake while adding the harness; it was corrected before the recorded verification run. No classifier rule or confidence requirement was weakened to make the scenarios match their evaluation labels.

## Follow-on acceptance criteria

### P0.4B — evidence contracts and deterministic analysis

- Implement typed evidence models, sanitized evidence collection, deterministic classification, synthetic evidence fixtures, and focused unit tests.
- Use an execution manifest only; no model calls and no executable browser scenarios are required in this milestone.
- Capture timestamped, stable-ID evidence and SHA-256 digests for finalized evidence files.
- Produce reports where missing or invalid evidence lowers confidence rather than manufacturing a classification.

### P0.4C — executable scenario evaluation

- Implement executable F01–F06 browser scenarios, including the local 503 server and ten-attempt F05 protocol.
- Add a repeatable evaluation harness that joins reports to the isolated evaluation manifest only after classification.
- Emit metrics and aggregate reports against controlled ground truth.
- Keep generated artifacts ignored and make the suite reproducible from documented commands.

### P0.4D — bounded Gemini investigation

- Add bounded, opt-in Gemini investigation only after P0.4B/C baselines exist; the existing Gemini SDK may be used.
- Evaluate evidence grounding against the deterministic baseline without allowing model output to alter deterministic findings or trigger actions.
- Add prompt-injection resistance tests using controlled hostile page/log text.

## Dependencies and unresolved decisions

Implementation depends on run-scoped execution/evaluation manifests, a deterministic clock/ID strategy, synthetic evidence fixtures, trace/screenshot digest tooling, and an explicit schema version. None exist today. F04 will use a Python standard-library HTTP server on `127.0.0.1` with an OS-assigned port. F05 will use ten attempts per declared condition, a fixed timeout, recorded injected delay, and the equivalence criteria defined above. Evidence collection will use allowlisted sanitized fields only; finalized local ignored evidence files receive SHA-256 digests, and missing or invalid evidence lowers confidence. Ground truth remains isolated in the evaluation manifest. P0.4B makes no model calls; P0.4D may use the existing Gemini SDK.

Open decisions requiring resolution before P0.4B:

1. What exact allowlisted browser/network field schema is sufficient for each F01–F06 scenario?
2. What clock/ID representation yields deterministic report normalization while retaining useful real capture timestamps?
3. What schema-version migration and local artifact retention policy should apply after P0.4 experimentation?
4. What P0.4D Gemini token/cost ceiling is acceptable after deterministic baselines are measured?

## Chronological engineering and design log

| Date | Milestone | Record |
| --- | --- | --- |
| P0.1 | Browser runtime | Local Playwright Chromium fixture established deterministic interaction, screenshot, trace, duration, and clean shutdown evidence. |
| P0.2 | Structured execution | Explicit bounded observe/propose/validate/execute loop, typed actions, state, mock planner, and execution log established. |
| P0.3A | Observations | Policy-derived, bounded observations separated page metadata from permissions and excluded sensitive input content. |
| P0.3B | Planner and repair | Gemini structured output was bounded and optional; independent assertion evaluation remained authoritative. A real `about:blank` click failure led to state-aware feasibility validation before click/fill execution. |
| 2026-11-01 | P0.4A | This document defines an evidence-first failure-intelligence contract and evaluation plan only. No P0.4 implementation has occurred. |
| 2026-11-01 | P0.4A finalization | Clarified controlled-only `APPLICATION_DEFECT` verification, digest limits, execution/evaluation manifest isolation, P0.4B/C/D boundaries, local F04/F05 feasibility, evidence allowlisting, ignored artifact handling, and deferred Gemini use. |
| 2026-11-01 | P0.4B.1 | Added the typed evidence-contract and integrity foundation only: collector-owned run/evidence IDs, injected UTC clock, allowlisted sanitization, canonical JSON, and SHA-256 modification detection for finalized sanitized evidence. No collector integration, classification, scenario injection, or model call was added. |
| 2026-11-01 | P0.4B.1 repair | Independent review identified mutable nested evidence facts, raw free-text exposure, and missing mapping/payload limits. Contracts now freeze nested facts, return defensive JSON copies, omit untrusted title/result text, require safe error categories, reject oversized mappings/payloads, and retain SHA-256 modification detection. These controls do not prove source authenticity or guarantee that every future sensitive datum is modeled safely. |
| 2026-11-01 | P0.4B.2 | Added a deterministic classifier only. It accepts same-run digest-valid evidence, explicit trusted assertion metadata, and typed repeated-attempt equivalence metadata; applies R01–R06 precedence; ignores `initial_category` and evaluation labels; and emits findings with rule IDs, evidence references, uncertainty, missing evidence, and reproducibility status. |
| 2026-11-01 | P0.4B.2 repair | Review found unsupported verified conclusions from contradictory validation/execution or assertion evidence, HTTP-status root-cause overclaiming, and incomplete repeated-attempt checks. R01 now preserves contradictions as insufficient evidence; R03 distinguishes a permitted-producer observed 5xx from a caller-convention trusted injected 503 context; and repeat attempts now require unique IDs, one matching digest-valid assertion record, and declared-outcome agreement. |
| 2026-11-01 | P0.4B.2 evidence-isolation repair | Review found that repeated-attempt analysis could index same-run evidence not named by the current `FailureEvent`, and duplicate evidence IDs could be silently overwritten. The classifier now validates each repeated-attempt reference against the event membership and its digest-valid event-scoped records only. Conflicting duplicate IDs are an R01 ambiguity, and all finding evidence references are derived solely from the validated event set. Identical duplicate records remain non-ambiguous but are not used to add duplicate references. |
| 2026-11-01 | P0.4B.3 | Added a standard-library synthetic evaluation harness for F01–F06 and six adversarial evidence cases. Execution scenarios contain no expected category or confidence; the private answer key is used only after classifier output. The executed synthetic run measured 6/6 category matches, 6/6 confidence matches, zero false `APPLICATION_DEFECT` findings, and 1/1 correct insufficient-evidence handling. F05 equivalence is declared only, and no browser, network, or Gemini execution occurred. A focused-test placement error found during implementation was fixed before final verification. |
| 2026-11-01 | P0.4C.1 design | Created the controlled-browser evaluation design only. It specifies local F01–F06 triggers, event-scoped sanitized evidence mapping, a loopback stdlib 503 server, fresh-context ten-trial F05 profiles, ignored artifacts, manifest isolation, cleanup, acceptance criteria, and implementation sequence. No P0.4C fixture, browser collector, server, classifier change, or browser execution was implemented or claimed. |
| 2026-11-01 | P0.4C.1 design correction | Corrected F05 identity semantics to match classifier run isolation: boundary trials use fresh browser contexts but one shared investigation `run_id`, unique attempt/evidence IDs, and one boundary `FailureEvent`; control-profile evidence remains a separate run and cannot contribute to boundary classification. |
| 2026-11-01 | P0.4C.2 | Added typed, versioned execution and evaluation manifest contracts only. Execution manifests exclude expected category/confidence, require artifact paths below `artifacts/p0.4c/`, and validate F05's shared boundary run, separate control run, and unique ten-trial identity sets. Evaluation manifests own the F01–F06 answer key separately. Five focused manifest tests passed; no fixture, browser, network, runtime, or classifier change was made. |
| 2026-11-01 | P0.4C.2 validation repair | Hardened manifest lexical validation before path normalization: empty, `.` and `..` segments are rejected; fixture paths must be valid relative HTML paths below `fixture/`; artifact paths remain below `artifacts/p0.4c/`; and execution/F05 run IDs cannot contain either path separator. Six focused manifest tests passed. |
| 2026-11-06 | P0.4C.3 | Added only three static controlled fixtures: visible-disabled F02, successful-action/incorrect-result F03, and bounded deterministic control/boundary-delay F05. Added Playwright fixture tests with fresh contexts, bounded waits, and explicit close paths. No evidence collector, server, classifier, manifest, or runtime behavior changed. |
| 2026-11-06 | P0.4C.4 | Added a minimal local Playwright collector for fixture scenarios. It emits run-scoped, digest-finalized observation/action/assertion evidence using allowlisted facts, event-scoped `FailureEvent` references, and canonical JSON only below the manifest's ignored P0.4C artifact directory. Focused browser tests cover F01/F02/F03 and one F05 control action; F04 and the 20-trial F05 evaluation remain deferred. No classifier rule, manifest, runtime, Gemini, or HTTP-server change was made. |
| 2026-11-06 | P0.4C.5 | Added F04 only: a bounded Python stdlib `ThreadingHTTPServer` on `127.0.0.1` with an OS-assigned port, deterministic 503 response, bounded Playwright navigation, safe status-only `NETWORK`/`http_server` evidence, and a same-run `TrustedInfrastructureContext`. The focused integration test supplies that context to the unchanged classifier and expects R03 verified infrastructure failure. No external request, response body/header storage, classifier rule, Gemini, or F05 repeat evaluation was added. |
| 2026-11-06 | P0.4C.6 | Added the fixed F05 protocol: ten fresh-context control trials at 0 ms and ten fresh-context boundary trials with the predeclared 25/300 ms schedule and 100 ms assertion timeout. Control and boundary use separate factories/runs/artifact groups; all boundary evidence remains in one event/run, and repeat metadata is constructed only from complete actual assertion evidence before invoking the unchanged classifier. The local environment lacks Playwright and loopback sockets, so no browser timing or classification result is claimed yet. |
| 2026-11-06 | P0.4C.7 | Added F06 as a real normal-fixture click whose browser context ends before post-action assertion collection. It retains only genuine pre-action/action evidence and names the unavailable same-run assertion slot in its event; no contradictory record, digest corruption, page text, or fabricated assertion outcome is added. The unchanged classifier is invoked and expected to apply R01 insufficient evidence. Browser validation remains pending because Playwright is unavailable locally. |
| 2026-11-06 | P0.4C.7 structure repair | Corrected result dataclass method ownership: `F05EvaluationResult` again owns its metrics and complete control/boundary serializer, while `F06EvaluationResult` retains an independent minimal serializer. Added direct regression coverage that calls both serializers without requiring Playwright. |
| 2026-11-06 | P0.4C.8 | Added the unified local browser evaluation runner. It constructs isolated execution manifests and run IDs, delegates F01–F06 to the existing collectors, invokes the unchanged classifier only with collected evidence and typed trusted context, then joins the separate evaluation manifest after classification. The sanitized report records per-scenario predictions, partial/incomplete outcomes, F05 control/boundary metrics, and aggregate accuracy/confidence/false-defect/insufficient-evidence metrics under ignored `artifacts/p0.4c/<evaluation-run-id>/`. The user's Windows/Playwright Chromium run verified 75/75 passing tests with zero skips and six real browser scenarios: 6/6 category matches, 6/6 confidence matches, zero false application-defect classifications, F05 control 10/0, and boundary 5/5 pass/fail. These are controlled local experiment results, not a general real-world reliability claim. |
| 2026-11-06 | P0.4C.9 | Hardened evaluation inputs and report persistence only: duplicate or unknown scenario outcomes and unsafe run IDs are rejected; an incomplete outcome or a completed outcome without a valid `ClassificationResult` cannot contribute to completion, accuracy, or confidence agreement; all metrics retain a fixed six-scenario denominator; and existing report/answer-key files cannot be overwritten. Codex's separate environment lacks Playwright/Chromium, so its browser-dependent tests remain skipped and its CLI reports incomplete scenarios; those results do not supersede the verified Windows browser run. |
| 2026-11-06 | P0.5 readiness audit | Completed a documentation-only Phase 0 architecture/readiness audit. It recommends a conditional Phase 1 go for product, security, persistence, and deployment foundation work, while retaining a no-go for public or multi-user browser execution until those P0 decisions are approved. The audit preserves verified controlled browser results, identifies documentation drift and missing production boundaries, and defines the smallest P0 closure tasks and Phase 1 entry criteria. |
| 2026-11-06 | P0.6 product definition | Defined the narrow Phase 1 MVP as a human-approved, project-scoped browser test execution and evidence-review workflow for controlled targets. It maps product requirements to Phase 0 prototypes without treating them as production features, explicitly defers arbitrary public targets, credentials, autonomous remediation, and LLM investigation, and establishes human approvals, evaluation gates, and unresolved product decisions. |
| 2026-11-06 | P0.7 security threat model | Defined the smallest defensible Phase 1 security boundary: authenticated project-scoped execution of anonymous, organization-controlled test targets through isolated workers with deny-by-default egress. It distinguishes Phase 0 local safeguards from required product controls, disables credentials and visual artifacts for the first hosted MVP, defers LLM use by default, and specifies SSRF, tenant-isolation, worker, audit, and acceptance requirements before remote execution. |
| 2026-11-06 | P0.8 backend execution design | Designed the Phase 1 durable backend: one FastAPI API, PostgreSQL system of record and initial job queue, and separate isolated browser workers. It defines project-scoped schema relationships, immutable policy versions, job/attempt leases and recovery, idempotency, evidence/finding persistence, API contracts, transaction boundaries, and acceptance tests while deferring Redis/Celery/Kafka, raw artifacts, and autonomous retries. |
| 2026-11-06 | P0.8 architecture corrections | Resolved durable-execution edge cases without changing the MVP topology. A terminal job is now immutable; an explicit operator retry creates a separate linked job and fresh attempt, while lease-loss recovery performs bookkeeping only and cannot enqueue replacement browser work. Database fencing is supplemented by supervisor-enforced browser/process-group shutdown and an independent execution deadline. The planned schema now uses composite project/attempt foreign keys for critical ownership relationships. Invalid or unpersistable evidence is discarded or recorded only as a safe operational finalization failure; it can never become authoritative evidence or support a defect conclusion. |
| 2026-11-06 | P0.9 deployment feasibility | Assessed local Compose and four practical hosted combinations using current official provider documentation. Local FastAPI/PostgreSQL/separate-worker development is feasible with pinned Playwright Chromium and Docker Desktop/WSL2, while stronger Hyper-V isolation is preferred for hostile-page testing. Hosted API/database prototyping is conditionally feasible, with AWS ECS/Fargate + RDS the leading candidate for an isolation proof of concept. Remote browser execution remains a no-go: no reviewed low-cost platform configuration independently proves controlled DNS, SSRF-resistant egress, per-attempt isolation, and forced browser termination. The record defines conservative initial limits, cost assumptions, and deployment acceptance tests. |
| 2026-11-06 | Phase 0 completion checkpoint | Finalized Phase 0 documentation without changing application code. The planned durable schema now explicitly enforces one attempt per job, documents the deferred circular `latest_attempt_id` migration, limits the worker to lease-scoped least-privilege database procedures, and makes retry lineage linear with no sibling retry jobs. The README now accurately separates the implemented local bounded-runtime/evidence/classifier/F01–F06 experiments from unimplemented Phase 1 infrastructure. Phase 0 remains a feasibility baseline: hosted browser execution is not approved until the P0.7/P0.9 isolation and egress acceptance tests pass. |
| 2026-11-06 | P1.1 FastAPI backend foundation | Added an isolated FastAPI application factory with explicit no-dependency lifespan, liveness/readiness endpoints, typed environment configuration, structured correlation-ID request logs, and safe JSON error handling. The foundation has no database, authentication, worker, browser, queue, or public execution endpoint; readiness reports only its initialized local API capability. Focused backend tests cover lifecycle, health, configuration rejection, error contracts, correlation logging, and secret exclusion while preserving Phase 0 tests. |
| 2026-11-06 | P1.1 focused test repair | Corrected the unexpected-error integration test after it treated FastAPI `TestClient`'s returned `httpx.Response` as an in-process Starlette `JSONResponse`. The client response exposes `.content` and `.json()`, not `.body`; the repaired test verifies HTTP 500, the `internal_error` code, and exclusion of the injected secret from both public bytes and structured logs. No application or Phase 0 behavior changed. |
| 2026-11-06 | P1.1 final review corrections | Moved security-sensitive backend settings invariants into Pydantic v2 field/model validators so direct `BackendSettings` construction cannot bypass production-debug, environment, log-level, service-name, or trusted-host checks. Replaced the shared `aegisqa.api` logger with a UUID-named non-propagating logger per application instance, preserving JSON field allowlisting while preventing one app factory from replacing another app's stream handler. Added focused direct-construction and interleaved multi-app logging regression coverage. |
| 2026-11-06 | P1.2 PostgreSQL foundation | Added a synchronous SQLAlchemy 2.x PostgreSQL manager with explicit per-unit-of-work sessions, bounded pool/connect settings, readiness probing, and shutdown disposal; no connection is made at import and no migrations run at API startup. Added Alembic's initial revision for UUID-backed users, projects, and revocable memberships with restrictive foreign keys, identity/slug uniqueness, role/status checks, and PostgreSQL partial uniqueness for one active membership. PostgreSQL integration tests are explicitly gated by a disposable `*_test` database URL; SQLite is not used as a substitute. No authentication, project API, jobs, queue, worker, or browser integration was added. |
| 2026-11-08 | P1.2 database foundation review corrections | Replaced startup-cached readiness with a bounded, per-request PostgreSQL `SELECT 1` probe in a worker thread: liveness stays process-only, readiness can observe post-start outage and recovery, and public responses expose neither connection details nor credentials. Aligned ORM metadata with the existing initial migration using named identity/slug unique constraints and explicit nonredundant lookup indexes. Hardened destructive PostgreSQL tests: they now require `AEGISQA_RUN_POSTGRES_INTEGRATION_TESTS=true`, a separately configured `postgresql+psycopg` `*_test` URL, live `current_database()` identity verification, and a target distinct from the configured development database; migration verification restores the disposable schema and environment on failure. API startup still never migrates the database. The controls protect local test invocation but do not replace external credential, network, backup, or production-change-management safeguards. |
| 2026-11-09 | P1.2 PostgreSQL integration-test safety corrections | Replaced suffix-based destructive-test trust with fail-closed disposable-target preflight. PostgreSQL integration tests now require explicit opt-in plus separately configured development and test URLs; they compare canonical URL targets (including `localhost`/loopback aliases and omitted/default port), read each server/database identity, reject equal or unverifiable targets, and reject any test database with user tables or non-system schemas. Each integration-test class creates a random schema, supplies it to both Alembic and ORM connections, and drops only that owned schema during cleanup or setup failure. Alembic accepts only the harness-generated schema format. No destructive PostgreSQL command was executed for this correction pending review of the safety controls. |
| 2026-11-09 | P1.2 database safety test repair | Corrected the owned-schema cleanup regression test to use `MagicMock`, whose context-manager protocol matches SQLAlchemy's `engine.begin()` use. Hardened the cleanup helper itself to reject any schema name outside the generated `aegisqa_it_<32-hex>` pattern before constructing an engine or issuing SQL; focused coverage now proves that an attempted `public` schema cleanup is refused and cannot open a connection. |
| 2026-11-09 | P1.2 Alembic transaction correction | Corrected isolated-schema migration sequencing for SQLAlchemy 2.x: `SET search_path` now receives an explicit commit before Alembic configures and begins its migration transaction. The generated-schema validation and `version_table_schema` remain unchanged, so upgrade/downgrade work stays scoped to the owned schema while Alembic retains transaction ownership for migration changes and rollback behavior. |
| 2026-11-19 | P1.2 PostgreSQL integration preflight correction | Updated disposable-target schema preflight for PostgreSQL 17 internals: `pg_toast`, numeric `pg_temp_*`, and numeric `pg_toast_temp_*` schemas are excluded as system namespaces. Any other schema and every existing user table still make the target non-disposable and fail closed. |
| 2026-11-19 | P1.3-A OIDC authentication foundation | Added configured, bounded JWKS-backed RS256 bearer-token verification with fixed issuer/audience validation, immutable verified principals, and safe authentication failure responses. Verified issuer/subject pairs are SHA-256-derived into the existing unique user identity field; verified email metadata and allowlisted profile fields support idempotent PostgreSQL provisioning and `GET /v1/me`. No password flow, header bypass, project authorization, membership API, refresh token, audit persistence, or provider configuration was added. |
| 2026-11-19 | P1.3-A provisioning test repair | Local authentication testing exposed that a newly constructed SQLAlchemy `User` has no Python-side `status` until a database server default is returned. OIDC provisioning now explicitly creates a new user as `active`, preserving the database constraint and making the new-account decision deterministic; existing disabled users still raise `DisabledAccountError` and are never reactivated. |
| 2026-11-20 | P1.3-A PostgreSQL authentication integration | Added opt-in real-PostgreSQL provisioning coverage inside the existing generated schema only. It verifies active first login, identity reuse, distinct identity keys, disabled-user rejection without reactivation, concurrent unique-identity recovery, and transaction rollback for failed creates/updates. The test adapter uses schema-bound sessions from the existing owned-schema engine; it never creates or cleans data outside that schema. |
| 2026-11-20 | P1.3-A PostgreSQL harness repair | Corrected a test-only helper placement defect: post-test environment assertions had been captured in the schema-bound session helper, where `self` is not a `TestCase`. Restored those assertions to their owning test and left the helper responsible only for session commit, rollback, and close. |
| 2026-11-20 | P1.3-B project and membership APIs | Added synchronous authenticated project and membership endpoints using verified provisioned users, project-scoped queries, administrator-only mutation, parent-row locks, active-membership constraints, revoke-and-regrant role history, self-change prevention, and last-administrator protection. No target, execution, worker, artifact, invitation, or audit API was added. |
| 2026-11-20 | P1.4-A durable execution persistence | Added only PostgreSQL execution-job, one-attempt, and append-only safe-event schema/migration foundations. The request snapshot is versioned JSON, idempotency and linear retry lineage are unique per required scope, composite foreign keys preserve project ownership, and status/check/index constraints support future queue, lease, and history operations. No execution API, worker, browser, lease implementation, or retry automation was added. |
| 2026-11-20 | P1.4-A ORM mapper repair | Renamed the declarative `ExecutionEvent` attribute from reserved `metadata` to `event_metadata` while explicitly retaining the PostgreSQL `metadata` column already defined by the migration. Added mapper-configuration regression coverage. |
| 2026-11-20 | P1.4-A execution persistence integration tests | Added owned-schema PostgreSQL tests for job/attempt/event persistence, project-scoped idempotency, one attempt per job, linear retry lineage, composite project foreign keys, invalid statuses, request/event JSON constraints, and rollback. Tests found and corrected empty versioned-request and empty event identity schema gaps; no API or worker behavior changed. |
| 2026-11-20 | P1.4-A integration isolation repair | Corrected the owned-schema PostgreSQL test setup to delete execution events, attempts, and jobs in child-first order before memberships, projects, and users. Added sequential-cleanup coverage; no shared schema, CASCADE, TRUNCATE, or production foreign-key change is used. |
| 2026-11-20 | P1.4-A migration reconciliation | Added forward-only revision `20261120_02` for checks introduced after deployed `20261120_01`: semantic request-schema version, nonempty request snapshot, and nonempty event correlation/category. It validates existing rows before adding each constraint and treats an already-present same-table constraint as a no-op. |
| 2026-11-20 | P1.4-B durable execution APIs | Added project-scoped authenticated submission, list/detail, cancellation, and manual retry services/endpoints using PostgreSQL job/attempt/event transactions and idempotency keys. Requests are bounded versioned orchestration-only JSON; database time establishes deadlines; lifecycle events expose no lease tokens or request internals. PostgreSQL integration coverage exercises replay/conflict behavior, queued cancellation, retry lineage, role rejection, and project isolation through the disposable-schema harness. No worker, claiming, lease renewal, browser, or automatic retry behavior was added. |
| 2026-11-20 | P1.4-B deadline repair | Replaced an invalid SQLAlchemy `func.make_interval` Python-keyword call with typed, parameterized PostgreSQL interval arithmetic: `NOW() + :deadline_seconds * INTERVAL '1 second'`. Deadline validation remains 1–3600 seconds, and regression coverage compiles both boundary values against the PostgreSQL dialect to verify database-time and bind-parameter use. |
| 2026-11-20 | P1.4-B creation and idempotency repair | Fixed execution creation ordering for models without ORM relationships: the transaction explicitly flushes job, then attempt, then submission event, preserving composite-FK integrity atomically. Integrity recovery now recognizes only the named project idempotency or retry-lineage unique constraints; FK, CHECK, and unrelated integrity failures propagate rather than becoming false idempotency conflicts. |
| 2026-11-20 | P1.4-C durable worker | Added a separate PostgreSQL polling worker using locked queued-row claims, cryptographic lease tokens, database-time expiry, fenced state/heartbeat writes, safe append-only events, deadline sweeping, and lost-lease recovery to `recovery_required`. It has no browser executor and deliberately finalizes an otherwise runnable claim as `failed`/`executor_unavailable`; it never fabricates success or creates a retry attempt. |
| 2026-11-21 | P1.4-D reliability verification | Hardened active-deadline sweeping so a heartbeating attempt cannot outlive the database execution deadline. Added opt-in PostgreSQL coverage for concurrent idempotent submission and claims, wrong-token fencing, lease-loss recovery idempotence, queued/active deadline expiry, no false success, and claim-event rollback. Public execution history remains allowlisted and excludes lease tokens, request snapshots, and raw database errors. |
