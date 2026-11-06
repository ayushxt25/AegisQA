# AegisQA MVP Definition

## Purpose and decision

This document resolves the Phase 0 product-definition blocker identified in `docs/phase-0-p0.5-readiness-audit.md`. It defines a deliberately narrow Phase 1 MVP; it does not claim that the MVP is implemented, secure for public use, or production-ready.

The MVP is an **evidence-first browser test execution and failure-review workflow** for a single authenticated engineering team. Its purpose is to make a small, approved browser test repeatable and its outcome reviewable. It is not an autonomous testing employee, a general web crawler, or a root-cause engine.

## 1. Problem statement and target users

Engineering teams can run browser checks, but a failure often leaves an ambiguous record: did the application behave incorrectly, did automation fail, did a dependency fail, or is the evidence incomplete? Teams then manually reconstruct browser state and cannot safely distinguish a verified fact from an inference.

AegisQA will help a small engineering team define an approved browser-testing objective, execute it within a bounded policy, preserve sanitized evidence, and present deterministic failure findings with their uncertainty and provenance.

### Primary users

| User | Need | MVP responsibility |
| --- | --- | --- |
| QA engineer / test owner | Define and run a high-value browser objective; inspect failures. | Creates the project and approved test definition; starts executions; reviews evidence and reports. |
| Application developer | Determine whether a reported failure is application behavior, automation, infrastructure, or insufficient evidence. | Reviews execution history, evidence references, deterministic finding, and missing evidence. |
| Project administrator | Control which team members, targets, and potentially sensitive artifacts belong to a project. | Approves project membership and target onboarding. This role requires Phase 1 authorization design before implementation. |

The first MVP does not target anonymous users, external customers without a project relationship, consumer testing, or broad multi-tenant self-service.

## 2. Core differentiation

AegisQA’s MVP differentiates on **bounded, evidence-grounded execution**, not on unconstrained model autonomy:

- A planner proposal is not authority. It must pass static policy and latest-observation feasibility validation before browser execution.
- Deterministic assertions remain the success authority; a model cannot declare a pass.
- Failure reports distinguish verified deterministic findings, evidence-supported inferences, and insufficient evidence.
- Each finding names supporting, contradictory, and missing evidence instead of presenting a free-form root-cause claim.
- Evaluation ground truth is separated from execution inputs, avoiding a benchmark that leaks expected labels into classification.

This is a product hypothesis. It does not claim superiority to existing testing tools until user research and comparative evaluation are performed.

## 3. Primary MVP user journey

The intended product journey is:

```text
Create project
  -> define approved testing objective
  -> request execution
  -> execute bounded browser test
  -> inspect sanitized evidence
  -> deterministic failure investigation
  -> review report and project history
```

### Journey details

1. **Create project.** An administrator creates a project and assigns the test owner and reviewers. The project owns all test definitions, executions, artifacts, evidence, and reports.
2. **Define testing objective.** The test owner records a precise objective, expected observable outcome, approved target, allowed actions/selectors, timeout, and data-sensitivity classification. The first MVP supports one deterministic assertion per test definition.
3. **Execute test.** A user explicitly starts an execution. The system creates a durable execution and attempt record, applies the approved policy, and sends the work only to an isolated worker after the Phase 1 security boundary exists.
4. **Inspect evidence.** A reviewer sees execution status, sanitized observation/action/assertion facts, evidence IDs/digests, and controlled artifact references permitted by project policy. Raw secrets, input values, cookies, request bodies, authorization headers, and unapproved artifacts are not displayed.
5. **Investigate failure.** The deterministic classifier produces a finding: `APPLICATION_DEFECT`, `AUTOMATION_FAILURE`, `INFRASTRUCTURE_FAILURE`, `FLAKY_OR_INTERMITTENT`, or `INSUFFICIENT_EVIDENCE`, with confidence, evidence references, uncertainty, and missing evidence. The report never treats a failed assertion alone as proof of an application defect.
6. **Review report/history.** Project members can review immutable execution/attempt history and compare retries as separate, linked execution jobs with fresh attempts. A retry does not overwrite original evidence or findings.

## 4. MVP functional requirements

The following are product requirements, not claims of current implementation. “Phase 0 basis” identifies reusable feasibility evidence.

| ID | Requirement | Acceptance criteria | Phase 0 basis |
| --- | --- | --- | --- |
| FR-01 | Project ownership | Every test, execution, artifact, evidence record, and report belongs to exactly one project; unauthorized access is denied. | Missing; requires Phase 1 identity/authorization. |
| FR-02 | Approved test definition | A test owner can define an objective, deterministic expected outcome, approved target, action policy, and timeout; invalid or overly broad definitions are rejected. | `ActionPolicy`, typed actions, and deterministic assertion patterns in `agent_runtime/`. |
| FR-03 | Explicit execution | An authorized user starts one execution; the system creates immutable execution and attempt IDs and exposes lifecycle status. | Local `ExecutionState` is implemented, but durable jobs are missing. |
| FR-04 | Bounded browser execution | The worker allows only the approved action vocabulary and target policy, applies explicit timeouts/action budget, and records safe execution outcomes. | `AgentRunner`, `validate_action`, `validate_runtime_action`, and `PlaywrightBrowserTool`. |
| FR-05 | Deterministic pass/fail | Success requires the test definition’s deterministic assertion, not a planner statement or report label. | `fixture_assertion()` and `ExecutionState.succeed()` semantics. |
| FR-06 | Evidence review | A completed attempt records sanitized, run-scoped evidence with provenance and integrity digest; reviewers can inspect references and safe facts. | `EvidenceRecord`, `FailureEvent`, canonical JSON, SHA-256 modification detection. |
| FR-07 | Failure investigation | A failed or incomplete attempt receives an unchanged deterministic finding where evidence is sufficient; otherwise it reports insufficient evidence. | `DeterministicClassifier` R01–R06. |
| FR-08 | History and retry lineage | Users can list project executions and attempts; an operator retry creates one new execution job with a fresh attempt linked to its source and cannot replace evidence. | Missing durable persistence/state model. |
| FR-09 | Controlled target onboarding | A project administrator approves each target before it is eligible for execution; the system records target policy/version. | Missing; Phase 0 policies are fixture-local only. |
| FR-10 | Controlled evaluation | The team can run the controlled F01–F06 regression suite separately from product executions and retain its versioned evaluation result. | `failure_intelligence/browser_evaluation.py` and P0.4C.8 Windows verification. |

## 5. MVP non-functional requirements

| Area | MVP requirement | Status at Phase 0 |
| --- | --- | --- |
| Security | Deny by default: no target, browser action, credential, artifact, or project data is accessible without explicit policy and authorization. | Partially prototyped only through local action/evidence policy. |
| Isolation | Browser work executes outside the API process with enforced network, filesystem, CPU, memory, and time limits. | Missing. |
| Privacy | Persist only allowlisted evidence facts; project policy controls trace/screenshot retention and access. | JSON evidence sanitization implemented; artifact-storage policy missing. |
| Integrity | Finalized evidence has deterministic serialization and SHA-256 modification detection; reports preserve references instead of rewriting evidence. | Implemented for `EvidenceRecord`; not an authenticity guarantee. |
| Reliability | Execution creation is idempotent; worker claim, cancellation, timeout, retry, and recovery transitions are durable and auditable. | Missing. |
| Observability | Operators can measure queue delay, execution duration, worker failure, artifact write failure, and recovery outcomes without exposing sensitive content. | Missing. |
| Usability | A reviewer can determine what happened, why the conclusion has its confidence level, and what evidence is missing without inspecting raw logs. | Prototype report structures exist; product UI/API missing. |
| Performance | Initial capacity is intentionally conservative: one browser context per attempt and no claim of parallel throughput until worker benchmarks exist. | Fresh contexts are exercised in P0.4C F05; no capacity benchmark. |

No availability, latency, concurrency, or cost SLO is promised until the Phase 1 deployment/worker decision and measurements exist.

## 6. Supported execution boundaries

### Initial MVP scope

- Chromium only, through Playwright.
- One explicitly approved deterministic browser objective per test definition.
- A small typed action vocabulary: navigate, click, fill, inspect, and finish.
- Explicit action budget and bounded navigation/action/assertion timeouts.
- Owner-approved controlled targets only. Until the remote-target threat model and worker isolation are implemented, this means local fixtures or other explicitly controlled non-public environments.
- Deterministic evidence classification; optional Gemini planning remains off by default and is not required by the MVP.
- Project-scoped sanitized evidence and reports once persistence/authorization is implemented.

### Explicit boundary conditions

- A target’s appearance in page content never grants a new permission.
- An LLM proposal never expands the approved action/target policy.
- A failed deterministic assertion is not automatically an application defect.
- A generic timeout is not automatically an infrastructure finding.
- SHA-256 detects later modification relative to a trusted recorded digest; it does not authenticate evidence capture or prove correctness.

## 7. Non-goals and deferred features

The following are intentionally outside the first MVP:

- Public self-service execution against arbitrary URLs.
- Production credentials, login automation, MFA bypass, payment flows, or secret vault integration.
- Cross-browser/device matrix testing, mobile emulation, visual-regression product features, or load testing.
- Autonomous retries, test repair, selector generation, remediation, pull-request changes, or deployments.
- LLM-generated investigation narratives (P0.4D), LLM-based severity decisions, or an LLM authority to change deterministic findings.
- General application root-cause analysis, incident management, ticketing integrations, dashboards, scheduling, and notifications.
- Multi-region/high-availability deployment and enterprise compliance claims.

Deferral is intentional: each item requires a security, product, or operations decision not established by the current feasibility evidence.

## 8. Human approval requirements for risky actions

| Action | Approval required | Rationale |
| --- | --- | --- |
| Create project / add member | Project administrator | Establishes data ownership and reviewer access. |
| Register or expand target host/path/network scope | Project administrator plus security policy validation | Prevents arbitrary target access and SSRF expansion. |
| Create/change a test definition or action policy | Test owner; administrator approval for scope expansion | Changes what the browser may do. |
| Enable `fill` for any potentially sensitive field | Explicit project administrator approval; no secret value storage in the initial MVP | Avoids credential and personal-data capture. |
| Start or retry an execution | Authorized test owner or reviewer | Keeps browser actions human-initiated in the MVP. |
| View/download screenshots, traces, or raw artifacts | Project policy and authorized reviewer | Artifacts can contain visual sensitive data despite JSON sanitization. |
| Enable Gemini planner or future investigation | Administrator opt-in after security approval | Provider calls and prompt-injection risk remain separate from deterministic execution. |

The MVP never permits an agent to broaden target scope, create credentials, alter a test definition, retry autonomously, change code, or deploy a change.

## 9. Success metrics and evaluation criteria

### Product success metrics to collect in Phase 1

- Number of approved test definitions that complete with a reviewable report.
- Execution completion rate, broken down by application, automation, infrastructure, and insufficient-evidence outcomes.
- Evidence completeness rate: required evidence records present, valid, and project-accessible.
- Reviewer outcome: whether a reviewer can accept, reject, or request more evidence for a finding.
- Time from execution completion to a reviewer’s understood outcome; collect it before setting a target.
- False application-defect rate, measured only against independently labeled controlled or pilot cases.
- Worker queue delay, attempt duration, timeout rate, cleanup failure rate, retry/recovery result, and artifact-storage failure rate.

### Minimum evaluation gates

1. Preserve the P0.4C F01–F06 controlled suite as a regression baseline. The recorded Windows P0.4C.8 run achieved 6/6 category matches, 6/6 confidence matches, zero false application-defect findings, and the declared F05 10/0 and 5/5 profiles. This is a baseline, not a real-world reliability estimate.
2. Before expanding beyond controlled targets, run the suite in a pinned browser/worker environment and retain the report with browser/worker versions.
3. Before making an `APPLICATION_DEFECT` finding visible as verified for a non-fixture target, require an independently trusted expectation, completed-action evidence, failed assertion evidence, and a reviewer-approved policy.
4. Do not set numerical reliability or cost promises from the current single controlled-environment evidence. Establish them through Phase 1 pilot and worker benchmarks.

## 10. Requirement traceability to Phase 0

| MVP need | Existing Phase 0 capability | Evidence | Product gap remaining |
| --- | --- | --- | --- |
| Bounded test action | Typed `BrowserAction` and `ActionPolicy` | `agent_runtime/actions.py` | Persisted/project-owned policies and target onboarding. |
| Safe action feasibility | Latest observation URL/element validation | `agent_runtime/runner.py`; P0.3B repair record | Worker isolation and stale-state/race handling. |
| Controlled observation | Policy-derived bounded element inventory | `agent_runtime/browser.py`; P0.3A record | Product observation schema/version and project policy. |
| Deterministic test outcome | Assertion evaluator controls terminal success | `agent_experiment.py`; `ExecutionState` | Test-definition persistence and richer assertion types. |
| Planner boundary | Mock planner and optional structured Gemini planner | `agent_runtime/planner.py`, `gemini_planner.py` | Product provider governance; optional planner is not an MVP dependency. |
| Sanitized evidence | Immutable evidence records, canonical JSON, digest verification | `failure_intelligence/contracts.py` | Artifact storage, access control, retention, authenticity controls. |
| Failure report | Deterministic R01–R06 classification | `failure_intelligence/classifier.py` | Durable report storage, reviewer workflow, real-target evaluation. |
| Evaluation discipline | Isolated ground truth and controlled F01–F06 harness | `failure_intelligence/manifests.py`, `browser_evaluation.py` | CI/browser-worker benchmark and product execution separation. |

## Unresolved product questions

1. Which first target class is acceptable after the threat model: a local internal staging application, a dedicated test environment, or another constrained option?
2. What data classifications can a first project hold, and are screenshots/traces permitted at all in the MVP?
3. Which project roles are sufficient for the first team: administrator, test owner, and reviewer, or a simpler owner/member model?
4. What assertion forms beyond one deterministic expected value are necessary for the first workflow without broadening the attack surface?
5. What pilot cohort and independently labeled test cases will measure reviewer usefulness and false-defect outcomes?

## Decision summary

The Phase 1 MVP is a human-approved, project-scoped, deterministic browser-test execution and evidence-review workflow for approved controlled targets. The MVP’s value is trustworthy separation of observed fact, deterministic finding, inference, and missing evidence. It will not claim autonomous testing, public web execution, or production reliability until the remaining security, persistence, and deployment gates are implemented and measured.
