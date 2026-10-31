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
| F05 intermittent rendering delay | Fixture records injected delay and runs ten controlled attempts for each declared condition using a fixed timeout. Equivalent attempts share fixture version, browser/runtime settings, action policy, timeout, and declared delay condition. | Mixed attempt outcomes, timings, injected delays, and target availability observations. | Delay pattern is injected. | `FLAKY_OR_INTERMITTENT`, `EVIDENCE_SUPPORTED_INFERENCE` after the ten-attempt protocol. | Calling one timeout proof of flakiness. |
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
