# P1.5 Isolated Browser Execution Architecture

## Decision and status

This document designs the next increment only. It does not enable a browser executor, alter the P1.4 worker, approve remote targets, or claim browser isolation is implemented.

P1.4 currently provides PostgreSQL jobs, one attempt per job, idempotent submission, lease-fenced worker writes, cancellation requests, deadlines, and recovery bookkeeping. Phase 0 provides the bounded `AgentRunner`, `ActionPolicy`, `PlaywrightBrowserTool`, deterministic assertion evaluator, sanitized evidence contracts, and deterministic classifier. The integration must reuse those components behind a separate child-process boundary.

## 1. Components and trust boundaries

```text
API / PostgreSQL worker (lease owner; no browser page)
  -- minimal, immutable attempt packet --> child process
  <-- bounded, sanitized result message --- child process
  -- lease-fenced final transaction ------> PostgreSQL

child process
  --> one fresh Chromium context --> approved test target only
```

| Component | Responsibility | Must not do |
| --- | --- | --- |
| API | Authorize project request and persist immutable versioned request. | Run Chromium or provide credentials. |
| Parent worker | Claim/renew lease, supervise child, check cancellation/deadline, persist final state. | Trust child result without validation; expose lease token. |
| Child executor | Run one bounded Phase 0 runtime in a fresh context and return sanitized typed output. | Connect to PostgreSQL, claim jobs, write files outside its attempt directory, retry jobs. |
| Network/sandbox enforcement | Constrain DNS, egress, process, filesystem, and privileges. | Rely on URL/selector checks as a network control. |

The child receives no database URL, database credentials, OIDC token, end-user data, cookies, credentials, or cross-project information. The parent retains the random lease token; it is never sent to the child, logged, or returned by an API.

## 2. Immutable execution input

P1.5 requires a future approved target/test-version resource before product browser execution. The current P1.4 `request_snapshot` is orchestration-only and deliberately has no target resource; it cannot be silently interpreted as one.

At dispatch, the parent validates a server-created immutable `BrowserAttemptPacket`:

```json
{
  "schema_version": "1.0.0",
  "project_id": "uuid",
  "execution_job_id": "uuid",
  "execution_attempt_id": "uuid",
  "target": {"origin": "https://test.example", "path_prefixes": ["/qa/"]},
  "action_policy": {"allowed_urls": [], "allowed_click_targets": [], "allowed_fill_targets": [], "allowed_inspect_targets": []},
  "assertion_contract": {"kind": "deterministic", "version": "..."},
  "limits": {"startup_ms": 10000, "navigation_ms": 10000, "action_ms": 5000, "overall_ms": 60000, "action_budget": 10}
}
```

The packet is derived only from project-scoped, administrator-approved target and immutable test-definition versions (not yet implemented), plus the durable job/attempt IDs. It is canonicalized, bounded, schema-validated, and recorded by fingerprint. It contains no ground-truth evaluation labels, raw HTML, unrestricted page text, credentials, or client-selected filesystem paths. A mismatch between packet IDs, project, policy version, or approved target prevents child launch and leads to a safe non-successful finalization.

## 3. Parent/child lifecycle

1. Parent claims the queued P1.4 attempt and obtains a lease.
2. Parent revalidates the job deadline, cancellation state, project ownership, target approval, packet schema, and network policy.
3. Parent creates one server-derived empty temporary directory and starts one restricted child process with a serialized packet over an inherited pipe/stdin. The parent records the process identity only in memory.
4. Child starts Chromium with one fresh browser/context, all permissions denied, downloads disabled, popup/new-page policy denied, and Phase 0 timeouts no greater than packet limits.
5. Child runs `AgentRunner` with `PlaywrightBrowserTool`, `ActionPolicy`, and the deterministic assertion evaluator. Policy validation and latest-observation feasibility validation remain mandatory.
6. Parent observes cancellation, lease renewal failure, child completion, startup/action/overall deadlines, and shutdown signals. Any stop signal ends scheduling immediately and terminates the complete child process tree.
7. Child sends at most one bounded result message, then exits. Parent validates it independently and writes terminal state/evidence only if it still owns the unexpired lease.
8. Parent removes the attempt directory and browser process tree. If final persistence fails, it reports no success; later P1.4 recovery marks the attempt lost/recovery-required without rerunning it.

### Termination

The parent is responsible for terminating the complete process tree, not merely a Playwright browser handle:

| Signal | Parent action | Durable outcome |
| --- | --- | --- |
| Startup deadline | Kill child tree; no browser result accepted. | `failed` or safe finalization failure while lease is valid. |
| Action/overall deadline | Kill child tree; discard incomplete raw payload. | `timed_out` with valid prior sanitized evidence only. |
| API cancellation | Kill child at the next parent boundary immediately; no new actions. | `cancelled` if lease-fenced finalization succeeds. |
| Lease renewal failure | Kill child tree immediately. | No terminal write by stale worker; sweeper marks `lost`/`recovery_required`. |
| Worker shutdown/crash | Stop accepting work, terminate children within cleanup grace. | Remaining live lease expires; sweeper recovers. |

Windows development uses `subprocess.Popen(..., creationflags=CREATE_NEW_PROCESS_GROUP)` and a Job Object (or equivalent tested process-tree supervisor) to terminate Chromium descendants. Linux deployment uses a separate process group/session, `PR_SET_PDEATHSIG` where available, and cgroup/container kill for the attempt. `terminate()` alone is insufficient on either platform. The deployment image must provide a tested supervisor implementation; unsupported platforms fail closed rather than running a browser in-process.

## 4. Browser and network policy

`ActionPolicy` is retained for navigation/action authority; `PlaywrightBrowserTool.observe()` remains policy-derived and bounded. It does **not** establish network safety. The hosted MVP permits only an anonymous, organization-controlled, approved HTTPS origin and approved path prefixes.

Before launch, and before every navigation/redirect, the parent/egress layer must canonicalize the URL and enforce exact scheme/host/port/path policy. Deny `file`, `data`, `blob`, `javascript`, custom schemes, IP literals, loopback, private/link-local/reserved addresses, internal suffixes, non-approved ports, cross-origin redirects, WebSocket/WebRTC, downloads, uploads, permissions, popups, clipboard, device/media access, service workers, proxy auto-discovery, and external network traffic.

Application checks cannot prevent SSRF or DNS rebinding. Enforce the connection with a controlled resolver plus firewall/egress proxy that allows only the approved, revalidated destination addresses and port; default-deny all other worker traffic. Browser sandboxing runs non-root with no host mounts, Docker socket, cloud credentials, shell capability, shared profile, or shared writable directory. Local `file://` fixtures and loopback F04 remain development-only controlled evaluations, never hosted target classes.

## 5. Child result and evidence contract

The child returns one `BrowserAttemptResult` over IPC, subject to a strict maximum message size and no retries:

```json
{
  "schema_version": "1.0.0",
  "attempt_id": "uuid",
  "outcome": "assertion_passed|assertion_failed|cancelled|timed_out|automation_failed|infrastructure_failed|finalization_failed",
  "assertion_verified": false,
  "duration_ms": 123,
  "safe_error_category": "optional_enum",
  "evidence": [],
  "failure_events": []
}
```

The child result contains finalized `EvidenceRecord`-compatible canonical facts only: source, producing component, UTC timestamp, run/evidence IDs, sanitized bounded observed facts, and SHA-256 digest. It excludes raw errors, URLs with sensitive query data, page text/HTML, response bodies/headers, cookies, input values, passwords, tokens, screenshots, traces, and browser internals. Parent validates schema, attempt/project association, size, sequences, sanitization status, canonical digest, and allowable component/source before persistence.

`assertion_passed` requires the existing deterministic assertion evaluator to have returned true after a completed action sequence. No transport success, child exit code, planner finish, or browser navigation can produce success. For a failed/non-successful result, the parent invokes the existing deterministic classifier only with verified event-scoped evidence and trusted assertion metadata. It preserves `APPLICATION_DEFECT`, `AUTOMATION_FAILURE`, `INFRASTRUCTURE_FAILURE`, `FLAKY_OR_INTERMITTENT`, and `INSUFFICIENT_EVIDENCE` boundaries; unsupported evidence remains insufficient evidence.

## 6. Fenced durability and recovery

Every terminal write is one transaction conditional on attempt ID, project/job IDs, active attempt state, exact lease token, and `lease_expires_at > NOW()`. It inserts sanitized evidence/failure-event memberships/finding, matching event, and terminal job/attempt state together. A zero-row update means ownership is gone: the parent discards the child result, kills its process tree, and writes nothing further.

| Condition | Browser result | Persistence behavior |
| --- | --- | --- |
| Verified assertion passes, lease valid | Success candidate | Persist `succeeded` only with all validated evidence and event in one transaction. |
| Assertion fails, lease valid | Failure candidate | Persist safe terminal outcome and deterministic finding. |
| Child crashes/invalid IPC | No authoritative result | Safe `failed`/`finalization_failed` only if lease valid; otherwise recovery later. |
| DB finalization fails after external assertion pass | Result is non-authoritative | Kill/cleanup; do not return success. Lease expiry becomes `lost`/`recovery_required`; no auto-rerun. |
| Stale worker/result | Discard | Zero fenced rows; no evidence, terminal state, or event from stale worker. |

Cancellation, deadline, and terminal writes lock/condition on the same attempt/job state. A terminal state wins permanently; a later cancel, heartbeat, sweep, or stale result makes no mutation or duplicate event. Manual retry remains the only operator-authorized new job.

## 7. Resource and artifact limits

Initial deployment defaults are configuration values pending measured capacity approval: one child/browser/context per worker, one active attempt per project, bounded global worker concurrency, action budget 10, and explicit startup/navigation/action/overall deadlines. The deployment ADR must set CPU, memory, PID, temporary-disk, file-descriptor, response-size, artifact/message-size, queue, and cleanup-grace limits and enforce them outside target-controlled JavaScript.

Each attempt has one fresh temporary directory and profile. The parent creates it under an attempt-owned root, never accepts child/user paths, and removes it on normal completion, failed IPC, cancellation, deadline, and worker restart reconciliation. Product MVP retains sanitized JSON only; screenshots, Playwright trace ZIPs, downloads, videos, and browser profiles are disabled.

## 8. Verification and acceptance criteria

### Unit tests

- Packet/result schema rejects cross-project IDs, unsafe URL schemes, oversized fields, raw error/page data, bad evidence digest, unknown outcome, and success without `assertion_verified`.
- Parent terminates child supervisor on each stop signal and never sends lease token over IPC/logs.
- Fenced finalization rejects wrong, expired, or superseded lease token with no event/evidence write.
- Existing static/action/runtime validation and assertion semantics remain unchanged.

### Controlled Chromium integration tests

- Run P0.1 fixture and F01–F06 from a child in a fresh context; preserve deterministic assertion/evidence outcomes.
- Enforce startup, action, overall deadline, cancellation, parent shutdown, and lease-loss termination; verify descendant Chromium processes are gone.
- Reject redirect, private/loopback, DNS-rebinding simulation, download, popup, permission, and disallowed-navigation cases before/at egress.
- Test event/evidence serialization limits, digest verification, database-finalization rollback, no stale finalization, and no fake success.

### PostgreSQL concurrency tests

- Concurrent claims/finalizations/cancellation/sweeper runs yield one terminal event/state and no replacement attempt.
- A child result arriving after worker crash/lease expiry is discarded; recovery is `lost`/`recovery_required`.
- Cross-project packet, attempt, evidence, and artifact paths are rejected at database and service boundaries.

Acceptance requires all controlled tests passing on Windows development and Linux deployment images, actual Chromium termination verification, externally enforced egress proof, no raw artifact retention, and no regression of Phase 0 controlled evaluation. Passing fixtures are not evidence that arbitrary websites or production/credentialed flows are safe.

## 9. Implementation increments and open limits

1. Add approved target/test-version models and immutable dispatch packet validation; keep browser disabled.
2. Implement parent/child IPC and platform-specific process-tree supervisor using a controlled fixture only.
3. Add lease-fenced result persistence and evidence/classifier adapter; retain no raw artifacts.
4. Add sandbox, controlled DNS/egress enforcement, and adversarial termination/redirect tests before any remote target.
5. Run controlled Chromium acceptance on Windows and Linux; require security review before enabling remote execution.

Open blockers: selected sandbox/egress platform, enforceable Windows Job Object implementation, Linux cgroup/container limits, target ownership approval workflow, concrete capacity limits, and target/test-version persistence. Until resolved and tested, P1.4’s no-executor worker remains the only permissible product behavior.

## P1.5-C1 containment status

Windows development now has enforceable Job Object limits for child-tree lifetime, process count, aggregate job memory, and hard CPU rate. The supervisor verifies Job Object setup and fails the child launch if it cannot assign the job. These limits do not deny outbound traffic, host-service access, DNS, filesystem access, or inherited host privileges; therefore `require_browser_containment()` remains fail-closed on Windows.

Linux deployment remains unsupported for browser launch in this repository. The required future boundary is a rootless container with a read-only image, per-attempt writable directory, dropped capabilities, cgroup CPU/memory/PID limits, no host mounts, and default-deny networking that permits only a controlled resolver and approved fixture endpoint. A configured flag is not evidence of that boundary: capability checks must verify effective controls before any browser child starts.

## P1.5-C2 restricted Docker sandbox status

The repository now contains a separate `backend.docker_sandbox` adapter and a dedicated diagnostic image recipe. The trusted worker would remain lease owner; the container is not given database URLs, lease tokens, API credentials, host mounts, Docker socket, or an execution-table connection. The adapter accepts only a resolved local immutable image ID (`sha256:<64 hex>`), never a tag or user command, and launches one fixed probe/wait entry point through the Docker CLI without a shell.

The effective Docker run contract is: Linux/amd64 daemon with builtin seccomp reported, `--network none`, non-root `65532:65532`, `no-new-privileges`, `--cap-drop ALL`, read-only root, bounded noexec/nosuid `/tmp`, PID/memory/CPU limits, no privileged mode, and no namespace/mount options. Immediately after start, Docker inspection must confirm these fields; otherwise the adapter force-removes only its generated container and fails closed. The controlled acceptance probe also verifies no parent database/lease value entered the container and that DNS, external TCP/UDP, host loopback, RFC1918 probe, and metadata probe are unavailable. Forced removal is the cancellation/deadline/lease-loss cleanup primitive; a container exit code is never an execution success signal.

This does **not** enable Chromium. The verified Docker Desktop daemon is not rootless, Docker's default bridge is intentionally unusable under `--network none`, and no reviewed fixture-only egress/resolver mechanism exists. A later increment must use a distinct, explicitly allowed fixture network (never host networking), revalidate redirects/destinations at an enforcement point, and demonstrate no path to Docker, PostgreSQL, host services, private networks, or metadata. Until then the existing browser containment gate stays closed and the worker remains `executor_unavailable`.

## P1.5-C3 controlled fixture networking status

For deterministic local development only, the Docker adapter can create a unique labeled internal bridge network and attach exactly two owned containers: a fixed sandbox entry point and a fixed HTTP fixture alias. It creates an identical **unapproved** fixture only on a different fresh internal network. Docker inspection requires the approved network membership set to be exactly those two owned containers before the probe proceeds. The sandbox never joins default, existing, host, or PostgreSQL networks; no ports are published and the Docker socket/host mounts remain absent. `--internal` blocks external routing but is not, by itself, a defense against a Docker control-plane administrator or a second container that the daemon attaches later; the daemon/control plane remains trusted.

The fixture identity (`aegisqa-controlled-fixture`), destination path, redirect test, network names, image IDs, and labels are adapter constants or generated by the trusted parent—not execution inputs. The child proves reachability only to `/allowed`, verifies the external redirect is rejected by application policy before follow, and probes denial of the unapproved fixture, known local PostgreSQL alias, host loopback, `host.docker.internal`, and public TCP. This is defense in depth around the network boundary, not a replacement for it. Fixture networking is HTTP-only and development-specific; it is explicitly separate from P1.5-A's HTTPS approved-target authorization and cannot authorize production targets.

Normal, cancellation, deadline, or failure cleanup force-removes only labeled generated containers then removes only labeled generated networks. Live Docker tests must confirm no owned resources remain. At the C3 checkpoint, browser execution remained disabled; C4 below adds only the fixed local fixture path. The local Docker daemon has not been verified rootless, and a browser compromise could still exploit daemon-level or kernel vulnerabilities outside the container policy.

## P1.5-C4 controlled Playwright status

One development-only, fixed Playwright 1.48.0/Chromium scenario is now wired to the owned fixture topology. The browser image is built from a pinned Debian Bookworm Python base and resolves to an immutable local image ID at runtime. It copies the existing Phase 0 runtime modules and invokes the existing bounded `AgentRunner`, `MockPlanner`, `ActionPolicy`, `PlaywrightBrowserTool`, runtime validation, and deterministic `Clicks: 1` assertion. The trusted-parent `FixtureExecutionRequest` is version `1.0`, bounded to 1–30 seconds, and accepts only `fixture_increment_v1`, `fixture_assertion_failure_v1`, or the timeout fixture; it does not accept public URLs, stored target IDs, selectors, expressions, or code.

The child allows only the fixed fixture origin/path at Playwright routing level and aborts its deliberate external redirect before follow. Docker remains the primary boundary: the fresh internal network admits only the fixture and sandbox, and Docker inspection verifies the network and C2 restrictions. Chromium required a still-bounded 1 GiB memory, one CPU, 256 PID, and 128 MiB tmpfs/shared-memory profile; all capabilities remain dropped, no-new-privileges/read-only filesystem/seccomp remain required, no host namespaces/mounts/socket/ports are used, and non-root execution is retained. The parent accepts a result only after strict schema validation, including provenance, assertion flag, bounded observation, redirect rejection, and scenario identity. An exit code is not success.

The live acceptance run completed the permitted assertion, produced a deliberate assertion failure without success, rejected the redirect, and force-cleaned timeout resources. This is **not** general browser enablement: P1.4 jobs still are not connected, P1.5-A targets remain unauthorized for this path, the Docker daemon is trusted/rootful, and no public or credentialed site may be automated.

## P1.5-D1 durable fixture dispatch

The opt-in worker fixture runtime is injected only when development/test configuration names immutable sandbox and fixture image IDs. After the existing `SKIP LOCKED` claim and lease-fenced `claimed → starting → running` transitions, it accepts only a bound P1.5-A job whose request schema is `1.0.0` and whose orchestration object is exactly `{mode: controlled_fixture, scenario_id: <fixed ID>}`. The approved target/test-version binding remains required for authorization and revocation handling, but no target origin is ever mapped into the container; fixture identity is entirely adapter-owned. Missing/legacy/arbitrary contracts become safe failed outcomes without Docker launch.

## P1.5-D2 durable evidence

`execution_evidence` is append-only and linked through the composite project/job/attempt foreign key. The Docker result remains untrusted: the parent checks scenario identity, bounded fields, assertion state, and digest-valid allowlisted Phase 0 evidence before inserting it in the same transaction as the lease-fenced terminal attempt/job transition and event. Child timestamps do not establish authority. Cancellation, deadline, lease loss, malformed output, and finalization rollback produce no authoritative evidence. Detail access is project-scoped and returns sanitized summaries only.

While Docker runs, the worker periodically renews its database lease and queries cancellation. Lease loss, cancellation, or deadline interrupts/removes the owned sandbox; no result is finalized after lease loss. Before finalization, cancellation and database deadline are checked again. Only validated `assertion_passed` plus `assertion_verified=true` is lease-fenced to `succeeded`; assertion failure, malformed/incomplete output, or containment failure maps to `failed`; timeout/cancellation map to their terminal states. The job/attempt terminal update and event remain one transaction. A finalization failure rolls back that transaction, leaving browser output non-authoritative for recovery; no automatic retry occurs.

## P1.5-D3 durable deterministic classification

`execution_classifications` has one immutable result per attempt and uses the composite project/job/attempt foreign key. After a failed terminal transition with committed evidence, the trusted parent reconstructs only exact-attempt, schema-1.0.0, allowlisted, SHA-256-valid Phase 0 `EvidenceRecord` values and invokes the existing `DeterministicClassifier`; Docker claims are never classification input. Invalid or unavailable persisted rows stay referenced by the failure event but are withheld from classifier evidence, so R01 fails closed. The stored outcome includes classifier version, rule identifier, category, confidence, bounded digest references, and bounded missing-evidence detail. It is advisory and cannot alter terminal execution state. Fixture assertion failures currently lack an independently authenticated expected-behavior contract, so R06 records insufficient evidence rather than an application-defect claim. Project-scoped detail returns only this bounded summary. Flaky classification and real target assertions remain deferred.

## P1.5-D4 classification recovery

The existing bounded worker sweep selects deterministically ordered failed job/attempt pairs that have committed evidence and no `execution_classifications` row. It snapshots and validates evidence without row locks, then uses a short insert transaction with the existing unique attempt constraint to resolve concurrent recovery. A crash or database failure before the advisory insert leaves the failed attempt eligible for the next bounded sweep; Chromium is never rerun and job, attempt, and evidence are never modified. Invalid/tampered records still create the explicit R01 insufficient-evidence outcome when classification storage is available. Recovery exceptions are deliberately non-authoritative and leave no partial classification/event transaction.
