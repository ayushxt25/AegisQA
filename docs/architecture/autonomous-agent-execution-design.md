# P1.6-A Autonomous Agent Execution Architecture

## Status and decision

This is a design only. The implemented fixture-only Docker child already uses Phase 0 `AgentRunner`, `ActionPolicy`, `PlaywrightBrowserTool`, a deterministic assertion evaluator, and `MockPlanner`. It does **not** dispatch a general agent from durable jobs, permit arbitrary targets/actions, or call Gemini from a worker or container.

The smallest safe next step is to make the existing fixed fixture path emit a versioned, parent-validated agent result. It remains `MockPlanner` only. Gemini planning is deferred until a separate provider-secret boundary is designed and tested.

### P1.6-B1 implementation status

`backend.agent_contracts` now implements the parent-side `FixtureAgentRequest` and `FixtureAgentResult` v1.0 validators. The request has canonical project/job/attempt/immutable-version UUID bindings, the one controlled fixture identity/origin, a fixed scenario set, a 1–10 action budget, zero model calls, a 1–30 second deadline, and the exact fixture action policy. The result is identity-bound and bounded to action kind/outcome summaries plus an approved-URL/element-count observation summary. Unknown fields, raw page data, secrets, unsupported capabilities, malformed values, budget/deadline violations, and inconsistent assertion claims are rejected.

This contract is not wired to `ExecutionWorker`, Docker dispatch, the sandbox child, evidence construction, or persistence yet. It does not enable Gemini or any new network capability.

### P1.6-B2 implementation status

The fixed Docker adapter now also exposes `run_agent_fixture()`. It revalidates a B1 request before container creation, launches only `agent_browser` in the same owned internal-network topology and C2 resource profile, and validates the final JSON through `FixtureAgentResult` before returning a bounded summary. The container receives only `AEGISQA_AGENT_REQUEST`, whose fields are the non-secret B1 packet; it receives no database URL, lease token, provider key, or general environment.

`contained_child.py` independently reparses the request before Chromium launch, uses the existing Phase 0 `MockPlanner`/`AgentRunner`/`PlaywrightBrowserTool` path, applies the existing fixed route restriction, and emits only action kind/outcome plus approved-URL/element-count summaries. It never treats child exit or planner completion as success: only the deterministic assertion evaluator emits candidate `assertion_passed`.

### P1.6-B3 implementation status

The existing worker now recognizes only exact `{ "mode": "controlled_fixture_agent", "scenario_id": <fixed ID> }` orchestration and derives a B1 packet after a project-scoped lookup of the already-bound approved target version and immutable definition version. The HTTPS target remains authorization metadata; it is not converted into a Docker destination. The fixture origin/policy is fixed in trusted worker code and is explicitly separate from P1.5-A target approval.

The result follows existing heartbeat, cancellation, deadline, containment-cleanup, and lease-fenced finalization paths. A validated agent assertion can succeed; a failed assertion records parent-created bounded Phase 0 observation/action/assertion evidence and proceeds to existing deterministic classification. Invalid contracts fail before Docker; child completion alone never succeeds. No public target, Gemini, secret, or client-provided agent packet is accepted.

## 1. Current component map

| Component | Existing responsibility | Trust level |
| --- | --- | --- |
| `ExecutionWorker` | Claims lease-fenced job; observes deadline/cancellation; finalizes durable state. | Trusted parent. |
| `DockerSandbox` | Starts/removes fixed non-root container and controlled fixture network; validates compact result. | Trusted containment adapter. |
| `sandbox/contained_child.py` | Runs fixed `fixture_*` scenarios, Phase 0 runtime, and returns one JSON result. | Untrusted browser child. |
| `AgentRunner` | Observe → propose → static validate → runtime validate → execute → observe. | Child library; output untrusted to parent. |
| `ActionPolicy` | Permitted URLs/selectors only; rejects arbitrary action vocabulary. | Immutable test-version input. |
| `PlaywrightBrowserTool` | Bounded, policy-derived observations; no passwords/hidden values/page HTML. | Child observation adapter. |
| `MockPlanner` | Fixed navigation/click sequence for fixture. | Deterministic test planner. |
| `GeminiPlanner` | Existing optional Phase 0 structured single-action provider adapter. | Not enabled in durable child. |
| evidence/classifier | Parent persists digest-validated records; Phase 0 classifier produces advisory result. | Trusted parent decision path. |

## 2. Proposed fixture-only boundary

```text
immutable job + approved target/test version
  -> worker validates binding, lease, deadline, cancellation
  -> Docker fixture sandbox (fixed scenario and policy)
  -> child: AgentRunner + MockPlanner + fresh Chromium context
  -> bounded AgentAttemptResult
  -> parent verifies identity, policy summary, assertion and evidence
  -> lease-fenced terminal state/evidence; advisory classification
```

The approved target/test version authorizes a durable request; it never lets a planner choose a URL, selector, script, network destination, or container option. Docker network/resource controls remain the security boundary. Playwright routing and `ActionPolicy` are defense in depth only.

## 3. Parent-child contract

Proposed `AgentAttemptRequest` v`1.0` is parent-derived and bounded:

```json
{
  "schema_version": "1.0",
  "fixture_scenario_id": "fixture_increment_v1",
  "objective": "controlled fixture assertion",
  "action_policy": {"allowed_urls": ["..."], "allowed_click_targets": ["#increment"], "allowed_fill_targets": [], "allowed_inspect_targets": []},
  "assertion": {"kind": "fixture_result_equals", "expected": "Clicks: 1"},
  "limits": {"action_budget": 3, "startup_ms": 10000, "action_ms": 5000, "overall_ms": 30000}
}
```

It contains no database URL, lease token, OIDC/API credential, cookies, user input, host path, arbitrary source code, or model key. The child receives only an allowlisted environment.

`AgentAttemptResult` v`1.0` must contain one bounded status (`assertion_passed`, `assertion_failed`, `automation_failed`, `timed_out`, `cancelled`, or `infrastructure_failed`), independent `assertion_verified`, action count, remaining budget, safe error category, and sanitized evidence candidates. It cannot contain raw exception text, action fill values, page text/HTML, response data, secrets, cookies, traces, or screenshots. Parent rejects unknown fields, bad identity/scenario/version, action count over budget, inconsistent assertion/outcome, oversized messages, and invalid evidence digests.

## 4. Lifecycle and authority

1. Worker claims and starts the durable attempt under its existing lease.
2. Parent derives the request solely from immutable server records and checks fixture mode/target approval/deadline/cancellation.
3. Child creates a fresh Chromium context and executes the Phase 0 loop. Observations are policy-derived and bounded; labels remain untrusted data.
4. Static validation runs before every action; runtime validation requires current approved URL plus available, visible, enabled element.
5. The deterministic assertion evaluator—not planner finish, browser exit, or child exit code—establishes candidate success.
6. Parent validates the result, then lease-fences evidence, event, and final job/attempt update. Only a verified assertion may become `succeeded`.
7. Failed committed evidence is classified by the existing deterministic classifier. Classification is advisory and never changes execution status.

## 5. Limits, interruption, and recovery

Action budget is at most 10 (fixture profile remains 3); model-call budget is zero initially. Startup/action/overall deadlines are parent-enforced and must not exceed the durable job deadline. Heartbeats run throughout Docker supervision.

| Event | Required behavior |
| --- | --- |
| Budget exhausted or planner `finish` without assertion | Non-successful agent result; no success finalization. |
| Cancellation/deadline | Parent removes owned container; cancellation/deadline wins over late result. |
| Lease loss | Parent removes container, discards result, writes no terminal result; existing sweeper recovers. |
| Malformed/oversized child result | Treat as infrastructure/automation failure; persist no child authority. |
| Final DB write failure | Browser output remains non-authoritative; no retry of browser actions. |
| Classification gap after finalization | Existing D4 sweep classifies committed failed evidence without rerunning Chromium. |

## 6. Failure classification and evidence

The parent creates/validates Phase 0 evidence records and SHA-256 digests; the child does not create authoritative classifications. Evidence is bound to one project/job/attempt and persisted only in a successful fenced transaction. Failed assertion alone is insufficient for `APPLICATION_DEFECT`; R04 requires independently trusted expected behavior and completed-action evidence. Missing, contradictory, tampered, or cross-attempt evidence fails closed to insufficient evidence.

## 7. Staged implementation and acceptance

1. **P1.6-B — contract adapter:** Add the parent-derived fixture-only agent request/result validator; retain `MockPlanner`. Tests: schema/bounds/secret exclusion/identity mismatch/action budget/invalid action rejection.
2. **P1.6-C — durable result mapping:** Persist bounded action/observation summaries through existing evidence flow. Tests: verified success, failed assertion, planner finish, budget exhaustion, lease loss, cancellation, deadline, malformed result, and atomic rollback.
3. **P1.6-D — deterministic evaluation:** Run existing controlled fixture scenarios through the durable agent path and compare only after classifier completion. Tests: no false success/defect, evidence isolation, cleanup, and recovery.
4. **Later opt-in provider design:** Decide whether a trusted parent-side broker can call Gemini without exposing API keys or untrusted page content. This requires separate cost, prompt-injection, audit, outage, and containment acceptance before implementation.

## Open questions

- How a production immutable assertion contract becomes independently trusted for R04 is unresolved.
- Whether provider calls occur in a trusted parent service or remain unavailable is unresolved; sending `GEMINI_API_KEY` into the sandbox is prohibited.
- Durable storage limits for action/observation summaries and the precise mapping of agent validation failures to existing event categories need acceptance tests.
- Fixture-only networking is development-only; this design does not authorize public or credentialed websites.
