# AegisQA Backend and Durable Execution Design

## Purpose and scope

This document designs the production-oriented backend required by the approved MVP and security threat model. It is documentation only. FastAPI, PostgreSQL, SQLAlchemy, and Alembic are proposed for Phase 1; none is currently implemented or installed.

The design preserves Phase 0’s bounded browser runtime, deterministic assertion semantics, evidence contracts, and classifier. It does not turn the local experiment into a public execution service. The first hosted MVP remains limited to authenticated, project-scoped execution of anonymous organization-controlled dedicated test targets.

## Status and core decisions

| Area | Status | Decision |
| --- | --- | --- |
| Local agent/runtime/evidence/classifier | Implemented Phase 0 prototype | Reuse behind durable interfaces; do not expose directly through an API. |
| API service | Planned Phase 1 | One FastAPI application owns authentication context, authorization, validation, and durable orchestration. |
| Persistence | Planned Phase 1 | PostgreSQL is the system of record for MVP entities, audit events, and sanitized evidence metadata. |
| Queue | Planned Phase 1 | PostgreSQL is the initial durable job queue, claimed with row locks and leases. No Redis/Celery/Kafka. |
| Browser worker | Planned Phase 1 | A separately deployed isolated worker polls PostgreSQL; it never serves API requests. |
| Migrations | Planned Phase 1 | SQLAlchemy models and Alembic migrations; schema changes are versioned and reviewed. |
| Raw visual artifacts | Deferred | MVP retains sanitized evidence/report metadata only; screenshots/traces are disabled. |
| LLM planning/investigation | Deferred by default | Existing Gemini prototype remains opt-in experimental code, not a Phase 1 product dependency. |

## 1. Backend components and responsibilities

```text
Client
  -> FastAPI API
       -> authentication + project authorization
       -> PostgreSQL (durable records, queue, audit, sanitized evidence)
       -> response/read model

PostgreSQL job claim
  -> isolated browser worker
       -> target policy and egress enforcement
       -> existing bounded runtime / deterministic classifier adapter
       -> sanitized evidence and terminal result transaction
```

### FastAPI API

- Validates authenticated caller identity and project membership before resource lookup.
- Owns project, membership, target, test-definition, execution-request, cancellation, retry, evidence/report read, and audit APIs.
- Performs no browser execution and holds no browser profile or target credentials.
- Validates request bounds, idempotency keys, and immutable policy/version references.
- Creates execution job plus initial attempt and audit event atomically.

### PostgreSQL

- System of record for all project-scoped state, queue claims, leases, events, sanitized evidence metadata, findings, and audit records.
- Provides `FOR UPDATE SKIP LOCKED` claim semantics and transaction isolation for the first worker deployment.
- Does not store raw page HTML, cookies, credentials, screenshots, traces, response bodies, or user-selected filesystem paths.

### Browser worker

- Polls and claims only eligible attempts from PostgreSQL.
- Reconstructs the immutable test-definition/target-policy snapshot stored on the attempt.
- Executes a bounded Playwright adapter in an isolated environment specified by the security threat model.
- Emits only sanitized evidence through a repository/service boundary; never writes directly to another project’s records.
- Uses a lease/fencing token for all heartbeat and terminal writes.

### Worker database authorization boundary

The browser worker uses a dedicated, least-privilege PostgreSQL role distinct from the API runtime role, migration owner, and recovery/sweeper role. It receives no end-user credential, PostgreSQL superuser capability, DDL privilege, role-management privilege, broad table-write privilege, or access to `users`, memberships, audit-history reads, arbitrary projects, or unclaimed attempts.

The worker role may execute a small audited database interface only:

1. `claim_next_attempt(worker_id)` atomically selects the next policy-eligible queued attempt with `FOR UPDATE SKIP LOCKED`, assigns the lease/fencing token, and returns that one attempt's immutable policy snapshot and project ID. It accepts no caller-chosen project, job, or attempt ID.
2. `heartbeat_attempt(attempt_id, lease_token, ...)`, `read_claimed_attempt(attempt_id, lease_token)`, `persist_terminal_result(attempt_id, lease_token, sanitized_payload)`, and `record_cleanup_outcome(attempt_id, lease_token, ...)` each require the matching unexpired lease and derive project/job ownership from the attempt row rather than trusting worker-supplied ownership fields.
3. The terminal-result interface validates evidence through the same bounded sanitizer/digest boundary and can insert only evidence, events, findings, and artifact metadata that reference the leased attempt's project. It cannot alter target approval, test definitions, memberships, retry lineage, prior terminal records, or another attempt's data.

Implement these as narrowly granted stored procedures or repository-facing views/functions, not direct worker access to base tables. The API role alone creates jobs and retries; the recovery role alone marks expired leases lost. Database constraints remain the final ownership guard, and service-layer project scoping remains required for all API reads. This boundary limits a compromised worker to the one attempt it has legitimately claimed; it does not make the worker or browser trusted.

### Phase 0 adapter layer

- Maps a durable test policy to the existing `ActionPolicy`, `ExecutionState`, `AgentRunner`, `PlaywrightBrowserTool`, `EvidenceRecord`, `FailureEvent`, and `DeterministicClassifier` interfaces.
- Keeps deterministic assertion evaluation authoritative.
- Does not persist the Phase 0 in-memory `ExecutionState` as the product job state; the durable state machine below is distinct.

## 2. PostgreSQL schema

All primary identifiers are application-generated UUIDs. Timestamps are UTC. Every project-owned table has a non-null `project_id`; immutable versions and attempts preserve the policy that actually ran.

### Identity and tenancy

| Table | Core fields | Constraints and indexes |
| --- | --- | --- |
| `users` | `id`, `identity_subject`, `email`, `display_name`, `status`, `created_at`, `last_authenticated_at` | Unique `identity_subject`; normalized-email uniqueness only if the selected identity provider guarantees it. Index active users. |
| `projects` | `id`, `name`, `slug`, `status`, `created_at`, `created_by_user_id` | Unique `slug`; check valid status; index creator/status. |
| `project_memberships` | `project_id`, `user_id`, `role`, `created_at`, `created_by_user_id`, `revoked_at` | Composite unique active membership per `(project_id, user_id)`; role enum: `administrator`, `operator`, `reviewer`; indexes by user and active project membership. |

### Target and test-definition model

| Table | Core fields | Constraints and indexes |
| --- | --- | --- |
| `targets` | `id`, `project_id`, `name`, `status`, `created_at`, `created_by_user_id` | Unique active name per project; target is a logical owned resource. |
| `target_versions` | `id`, `target_id`, `project_id`, `version`, `origin`, `allowed_path_prefixes`, `policy_json`, `approval_state`, `approved_by_user_id`, `approved_at`, `created_at` | Unique `(target_id, version)`; origin/policy validation occurs before insert; only an approved version can dispatch. Index `(project_id, approval_state)`. |
| `test_definitions` | `id`, `project_id`, `name`, `status`, `created_at`, `created_by_user_id`, `current_version_id` | Unique active name per project; index `(project_id, status)`. |
| `test_definition_versions` | `id`, `test_definition_id`, `project_id`, `version`, `objective`, `assertion_spec_json`, `action_policy_json`, `timeout_policy_json`, `planner_mode`, `created_at`, `created_by_user_id` | Unique `(test_definition_id, version)`; immutable after creation; checks action budget/timeouts/planner mode and no credentialed fields in MVP. |

`target_versions` and `test_definition_versions` are snapshots. Editing creates a new version rather than mutating a prior execution’s meaning.

### Durable execution and attempts

| Table | Core fields | Constraints and indexes |
| --- | --- | --- |
| `execution_jobs` | `id`, `project_id`, `test_definition_version_id`, `target_version_id`, `requested_by_user_id`, `retry_of_execution_job_id` nullable, `idempotency_key`, `request_fingerprint`, `status`, `cancel_requested_at`, `created_at`, `started_at`, `finished_at`, `latest_attempt_id` | Unique `(project_id, idempotency_key)`; unique nullable `retry_of_execution_job_id` makes retry lineage linear (one direct retry job per source); `request_fingerprint` detects same-key/different-request conflicts; indexes `(status, created_at)`, `(project_id, created_at DESC)`, `(project_id, test_definition_version_id)`. |
| `execution_attempts` | `id`, `project_id`, `execution_job_id`, `attempt_number`, `status`, `lease_token`, `leased_by_worker_id`, `lease_expires_at`, `heartbeat_at`, `started_at`, `finished_at`, `timeout_at`, `terminal_reason`, `policy_snapshot_json`, `created_at` | **Unique `execution_job_id`** enforces exactly one attempt per MVP job; `CHECK (attempt_number = 1)` keeps the recorded number honest; unique non-null `lease_token`; index claim path `(status, created_at)` with partial predicate for `queued`; indexes `(lease_expires_at)` for active states and `(project_id, created_at DESC)`. |
| `execution_events` | `id`, `project_id`, `execution_job_id`, `attempt_id`, `sequence`, `event_type`, `occurred_at`, `actor_type`, `actor_id`, `safe_payload_json` | Unique `(attempt_id, sequence)`; append-only; index `(attempt_id, sequence)` and `(project_id, occurred_at DESC)`. |

An execution job represents one user-visible execution request. An attempt represents exactly one worker lease/browser context. **A terminal job is never reopened.** A manual retry creates a distinct queued job and its one fresh attempt, records `retry_of_execution_job_id` on the new job, and never changes prior evidence or findings. `UNIQUE (retry_of_execution_job_id)` allows at most one direct retry job from a source job, including after that retry is terminal; a further retry must name the latest terminal retry job as its source. This intentionally creates a linear, auditable retry chain instead of independent retry fan-out. Automatic retries are not in the MVP.

### Evidence, findings, and artifacts

| Table | Core fields | Constraints and indexes |
| --- | --- | --- |
| `evidence_metadata` | `id`, `project_id`, `attempt_id`, `evidence_sequence`, `source`, `producing_component`, `captured_at`, `sanitization_status`, `observed_facts_json`, `content_digest`, `schema_version`, `artifact_id` nullable | Unique `(attempt_id, evidence_sequence)`; unique `(attempt_id, content_digest)` only if duplicate evidence is prohibited by policy; index `(attempt_id, evidence_sequence)`. Facts must pass the Phase 0 sanitizer before persistence. |
| `failure_events` | `id`, `project_id`, `attempt_id`, `stage`, `occurred_at`, `observed_facts_json`, `schema_version` | Index `(attempt_id, occurred_at)`; event-to-evidence membership stored in `failure_event_evidence`. |
| `failure_event_evidence` | `project_id`, `attempt_id`, `failure_event_id`, `evidence_id` | Composite primary key; composite foreign keys require the same project and attempt as both the failure event and evidence. |
| `investigation_findings` | `id`, `project_id`, `attempt_id`, `failure_event_id`, `rule_id`, `classification`, `confidence`, `reproducibility`, `uncertainty`, `missing_evidence_json`, `created_at` | One current deterministic finding per failure event for MVP; append a superseding finding only through a versioned future design. Index `(project_id, classification, created_at DESC)`. |
| `finding_evidence_references` | `project_id`, `attempt_id`, `finding_id`, `evidence_id`, `relation` | Composite primary key; composite foreign keys require the same project and attempt as both the finding and evidence; `relation` enum: supporting/contradictory. |
| `artifact_metadata` | `id`, `project_id`, `attempt_id`, `kind`, `storage_key`, `content_digest`, `size_bytes`, `retention_until`, `created_at`, `deleted_at` | MVP permits only sanitized report/evidence exports; raw visual kinds are rejected. Unique safe server-derived storage key; index retention/deletion lifecycle. |
| `audit_logs` | `id`, `project_id` nullable, `actor_id` nullable, `actor_type`, `action`, `resource_type`, `resource_id`, `occurred_at`, `outcome`, `reason_category`, `safe_metadata_json` | Append-only; indexes `(project_id, occurred_at DESC)`, `(actor_id, occurred_at DESC)`, and action/outcome for abuse review. |

Foreign keys are restrictive for execution history: user/project/test/target versions cannot be hard-deleted while referenced. Project deletion is a scheduled retention workflow, not a cascade. Every project-scoped principal table has `UNIQUE(project_id, id)` (and, where a child must share an attempt, `UNIQUE(project_id, id, attempt_id)`). Critical child relationships use composite foreign keys that include `project_id`: version-to-parent, job-to-version, attempt-to-job, event/evidence/failure-event/artifact-to-attempt, and finding/membership-reference-to-failure-event/evidence. This makes a cross-project ID pairing impossible at the database layer instead of relying solely on service validation. The repository layer still scopes every query by project, and any future PostgreSQL row-level security is defense in depth rather than a substitute for either control.

### Safe migration of `latest_attempt_id`

`execution_jobs.latest_attempt_id` and `execution_attempts.execution_job_id` form a deliberate circular relationship: the job must name its one attempt, while the attempt must belong to that job. The migration must not weaken the one-attempt invariant or leave a transient orphan visible to another transaction.

For a new installation, create `execution_jobs` and `execution_attempts` first, add `UNIQUE (execution_attempts.execution_job_id)` and `UNIQUE (project_id, execution_job_id, id)`, then add a **deferrable, initially deferred** composite foreign key from `(execution_jobs.project_id, execution_jobs.id, latest_attempt_id)` to `(execution_attempts.project_id, execution_attempts.execution_job_id, id)`. `latest_attempt_id` is non-null. The API generates the job and attempt UUIDs before writing, inserts the job with that future attempt ID, inserts the attempt in the same transaction, and commits; PostgreSQL validates the circular reference only at commit.

For an upgrade containing rows, first add `latest_attempt_id` nullable and the foreign key as `NOT VALID`, backfill only rows with exactly one same-project attempt, validate the constraint after an audit query reports no missing or mismatched rows, then set `NOT NULL`. Do not guess a latest attempt for a job with zero or multiple attempts: halt the migration, repair or archive the inconsistent data under an operator-approved maintenance procedure, and record the repair. The migration role alone receives DDL authority; application and worker roles do not.

## 3. Entity relationships and data invariants

```text
user --< project_membership >-- project
project --< target --< target_version
project --< test_definition --< test_definition_version
project --< execution_job --< execution_attempt --< evidence_metadata
                                    |                 --< failure_event --< finding
                                    |                 --< execution_event
                                    --< artifact_metadata
project --< audit_log
```

Required invariants:

- All linked target/test/job/attempt/evidence/finding records share the same `project_id`, enforced for critical relationships by the composite foreign keys above rather than asserted only by application code.
- `execution_jobs` reference immutable, approved target and test-definition versions; attempts store a redundant immutable policy snapshot for forensic stability.
- `UNIQUE (execution_attempts.execution_job_id)` plus the non-null, deferred `latest_attempt_id` relationship enforce exactly one attempt per MVP job; a later multi-attempt-per-job design requires a new state-machine/lineage review and migration, and is not implied by recovery.
- A terminal attempt is immutable except retention/deletion metadata and append-only audit/event records.
- `failure_event_evidence` and finding evidence references may point only to evidence from the same attempt/project.
- API responses use pagination and stable order; project IDs are authorization scope, not opaque routing hints.

## 4. REST API contracts

All endpoints are prefixed `/v1`, require authentication, use JSON, and return only authorized project-scoped resources. Request/response examples are schemas, not an implemented API.

| Method and path | Role | Request / response contract |
| --- | --- | --- |
| `POST /v1/projects` | Authenticated creator | Request `{name, slug}`; response `201` project with ID/role. |
| `GET /v1/projects` | Member | Cursor-paginated visible projects only. |
| `POST /v1/projects/{project_id}/members` | Administrator | `{user_id, role}`; creates or updates active membership with audit event. |
| `POST /v1/projects/{project_id}/targets` | Administrator | `{name, origin, allowed_path_prefixes}`; response target/version in pending or approved state after validation. |
| `POST /v1/projects/{project_id}/targets/{target_id}/versions/{version}/approve` | Administrator | Explicit approval; response approved immutable target version. |
| `POST /v1/projects/{project_id}/tests` | Operator | `{name, objective, assertion_spec, action_policy, timeout_policy}`; creates test and immutable version. |
| `POST /v1/projects/{project_id}/tests/{test_id}/versions` | Operator | Same bounded definition fields; creates next version, never mutates prior version. |
| `POST /v1/projects/{project_id}/executions` | Operator | `Idempotency-Key` header plus `{test_definition_version_id, target_version_id}`; returns `202` `{execution_job_id, attempt_id, status:"queued"}`. Same key/same fingerprint returns original response; mismatch returns `409`. |
| `GET /v1/projects/{project_id}/executions` | Member | Cursor-paginated job summaries, filters restricted to safe fields. |
| `GET /v1/projects/{project_id}/executions/{job_id}` | Member | Job, current/attempt history, terminal status, safe summaries. |
| `POST /v1/projects/{project_id}/executions/{job_id}/cancel` | Operator | Idempotent cancellation request; response `202` with current status. |
| `POST /v1/projects/{project_id}/executions/{job_id}/retry` | Operator | Requires a retry-specific `Idempotency-Key`. Only an operator may retry a terminal or recovery-finalized job. It creates one **new** queued job and initial attempt with `retry_of_execution_job_id=job_id`; a direct retry already present returns `409 retry_already_exists`, so later retries follow the terminal retry child. The original job is unchanged. No automatic retry. |
| `GET /v1/projects/{project_id}/attempts/{attempt_id}/evidence` | Member | Paginated sanitized evidence metadata only. |
| `GET /v1/projects/{project_id}/attempts/{attempt_id}/report` | Member | Deterministic finding/report with evidence references and missing evidence; never evaluation ground truth. |
| `GET /v1/projects/{project_id}/audit-logs` | Administrator | Paginated safe audit records. |

Errors use safe codes such as `not_found`, `forbidden`, `invalid_policy`, `idempotency_conflict`, `retry_already_exists`, `target_not_approved`, `execution_not_retryable`, and `rate_limited`. API error text never includes secrets, raw target response content, other-project resource existence, or worker exception text.

## 5. Project-scoped authorization boundaries

1. Authenticate before parsing resource-specific behavior beyond basic request bounds.
2. Resolve membership for `(caller, project_id)` once per request and pass an immutable authorization context to services/repositories.
3. Repositories require `project_id`; no unscoped `get_by_id` path is exposed to API handlers.
4. Workers receive project ID and immutable version IDs in a claimed attempt, but do not receive end-user session authority.
5. Evidence, reports, and audit reads authorize project membership before object metadata is loaded; artifact storage is never addressed directly by client input.
6. Background recovery uses a system actor but remains project-scoped and writes audit events.

## 6. Execution state machine

### Job state

```text
queued -> running -> succeeded | failed | cancelled | timed_out
queued -> cancelled
running -> cancellation_requested -> cancelled | failed | timed_out
running | cancellation_requested -> recovery_required -> failed
```

`succeeded`, `failed`, `cancelled`, and `timed_out` are terminal job states. `recovery_required` is an internal, non-executing state entered after a lost lease, worker failure, or unavailable terminal finalization. Recovery may record safe bookkeeping and finalize the job as `failed`; it must never move the job back to `queued` or start another browser attempt. Only the explicit retry endpoint may create a new job after terminal/recovery finalization.

### Attempt state

```text
queued -> claimed -> starting -> running
queued -> cancelled
claimed | starting | running -> cancelling -> cancelled
claimed | starting | running -> succeeded | failed | timed_out | finalization_failed | lost
```

Terminal attempt states are `succeeded`, `failed`, `cancelled`, `timed_out`, `finalization_failed`, and `lost`. `finalization_failed` means the browser result could not be safely made authoritative because validation rejected evidence or the terminal persistence workflow could not complete. A worker may transition only its own non-expired lease token. `execution_jobs.latest_attempt_id` is set atomically when the job and its initial attempt are created, never changed after that in the MVP, and always references an attempt from the same project/job through composite foreign keys. The service derives a job terminal state only from that attempt in the same transaction that records a safe terminal event/finding.

## 7. Scheduling, claiming, leases, and heartbeats

### PostgreSQL queue algorithm

1. API generates both UUIDs, then in one transaction inserts a queued `execution_job` whose non-null `latest_attempt_id` names its future attempt, inserts that queued attempt number one, and records an `execution_requested` event and audit entry. The deferred circular foreign key verifies the pair at commit.
2. A worker claims one eligible attempt using `SELECT ... FOR UPDATE SKIP LOCKED`, ordered by `created_at`, filtered on `status='queued'` and target approval.
3. In that same transaction it sets `status='claimed'`, a cryptographically random `lease_token`, worker ID, `lease_expires_at`, and claim event.
4. The worker transitions to `starting` then `running` using conditional updates requiring matching `attempt_id`, `lease_token`, active state, and unexpired lease.
5. Heartbeats extend the lease only for the matching worker/token; a failed conditional update is a stop signal. The worker stops scheduling actions, kills its browser process group, revokes that sandbox's egress where supported, and exits.
6. A recovery sweeper identifies expired active leases, marks that attempt `lost`, emits a safe event/audit record, and moves the job to `recovery_required`. It never assumes completion from an absent heartbeat and never creates a replacement attempt.

Lease fencing protects database ownership, not the browser process: a stale process might otherwise continue interacting with a target after its writes are rejected. Each attempt therefore has an independent, non-extendable execution deadline enforced by a worker supervisor outside the browser child process. The supervisor must terminate the isolated browser/process group at the deadline or after lease expiry plus a short cleanup grace period, even if the worker is wedged. The worker itself also terminates its child immediately when it detects failed fencing. The Phase 1 worker-isolation acceptance tests must demonstrate that a lease-lost attempt cannot continue using the browser or egress beyond that bound.

This design is intentionally polling-based and single-database. It trades low-latency push dispatch for simpler deployment, transactions, observability, and recovery during MVP. Queue load and polling interval must be benchmarked before capacity commitments.

## 8. Timeouts, cancellation, recovery, retries, and idempotency

### Timeouts and cancellation

- The API writes `cancel_requested_at` transactionally and emits an event. Workers poll it at action boundaries and heartbeat intervals.
- The worker stops new browser actions after cancellation, attempts bounded cleanup, and records `cancelled` or `failed` with a safe cleanup category.
- Deadline expiry transitions the attempt to `timed_out` only when the current lease token still owns it. A late/stale worker cannot overwrite terminal state.
- Browser process loss, lease loss, or cleanup failure creates an event with a safe category; raw exception text is never persisted.
- Lease loss is not a retry signal. The worker must terminate its isolated browser execution as described above; recovery only records loss/finalization state and requires an operator to authorize a new job.

### Retry policy

- No autonomous retry in the first MVP.
- An operator may request a retry only after a terminal or recovery-finalized job and only if the test/target version remains approved. The request requires its own project-scoped idempotency key; repeating that key returns the same retry job, while a key reused with a different retry request conflicts.
- Retry creates a new job, initial attempt, fresh browser context, fresh evidence IDs, and fresh lease, with `retry_of_execution_job_id` set to the source job. It does not reopen, mutate, or delete the source job, attempt, evidence, or findings.
- The MVP permits no retry fan-out: `UNIQUE (retry_of_execution_job_id)` means a source has at most one direct retry job for its lifetime, not merely one active retry. If that child is terminal, an operator may retry the child to extend the linear lineage; attempting a second sibling returns `retry_already_exists`.
- A later policy may define eligibility for infrastructure/transient failures, but it must be explicit and user-visible.

### Idempotency and duplicate prevention

- `POST /executions` requires an idempotency key scoped to project. The API stores a canonical request fingerprint in the job-creation transaction.
- Repeating the same key and fingerprint returns the original job/attempt; the same key with a different fingerprint is `409 idempotency_conflict`.
- One-attempt-per-job constraint plus the claim lease prevents concurrent duplicate browser work for one job.
- Fencing tokens prevent an expired worker from writing a result, but do not itself stop browser execution; supervisor-enforced shutdown and independent deadlines provide that separate control.

## 9. Transaction boundaries and concurrency

| Operation | Transaction boundary | Concurrency behavior |
| --- | --- | --- |
| Create project/membership/target/test version | Resource row(s), validation result, audit event | Unique constraints and optimistic version checks return conflict rather than last-write-wins. |
| Create execution | Job + first attempt + request event + audit event + idempotency record | Atomic; unique idempotency key resolves request races. |
| Claim attempt | Locked queue row + lease fields + event | `SKIP LOCKED` lets workers claim distinct attempts without a separate queue. |
| Heartbeat/cancel | Conditional update on attempt ID, state, lease token | Stale worker/late heartbeat affects zero rows and must stop. |
| Persist terminal result | Validated evidence metadata + event membership + finding + terminal attempt/job state + audit event | One transaction after sanitizer/digest validation; any failure rolls back the claimed result and does not make invalid evidence authoritative. |
| Manual retry | New job + initial attempt + retry-lineage audit event | Locks the source job; unique nullable `retry_of_execution_job_id` and retry idempotency prevent concurrent or later sibling retries. |
| Recovery finalization | Lost/finalization-failed attempt + safe job state/event/audit | No new attempt or browser execution; locks the job and records only the observed operational condition. |
| Retention deletion | Mark deletion intent, delete storage object, then mark completion/audit | Retriable and idempotent; no broad cascades. |

The MVP stores only sanitized JSON evidence in PostgreSQL. If a later artifact object store is introduced, use a staging upload and database metadata commit protocol; orphan cleanup must be explicit. Do not claim atomic cross-system storage without an outbox/reconciler design.

## 10. Evidence and artifact persistence lifecycle

1. Worker constructs evidence through the existing sanitizer and canonical/digest logic before persistence.
2. Persistence validates project/attempt ownership, monotonic evidence sequence, schema version, digest, payload bounds, and only allowlisted fields.
3. Only after validation passes does one transaction persist evidence metadata, failure-event/evidence membership, a deterministic finding, terminal attempt/job state, and audit event.
4. On evidence validation rejection, discard the invalid payload and raw error. If the database is available, finalize the attempt as `finalization_failed` and job as `failed` using a safe categorical reason and, at most, a finding of `INSUFFICIENT_EVIDENCE` built only from valid persisted records and explicitly missing evidence. Invalid evidence, its digest, and any conclusion derived from it are never authoritative.
5. If the terminal database transaction itself fails, roll it back completely; the worker must not report success, must terminate/clean up the browser, and must not publish an evidence-backed finding. Lease recovery later records `lost`/`recovery_required` with a safe operational category. That bookkeeping is not a replacement run and cannot turn an unavailable or invalid payload into authoritative evidence.
6. API exposes only sanitized facts and project-authorized report/evidence views.
7. Retention scheduler selects records past `retention_until`, records deletion intent, removes eligible data, and emits audit events. The initial product policy is 30 days for sanitized metadata; visual artifacts are prohibited.

## 11. Operational metrics and structured audit events

### Metrics

Track project-safe aggregate counters/histograms for:

- queued, claimed, running, terminal, cancelled, timed-out, lost, and recovery-required attempts;
- queue delay, claim-to-start delay, action duration, total attempt duration, cleanup duration, and heartbeat/lease failures;
- classifier category/confidence counts, evidence completeness/digest rejection counts, and insufficient-evidence rate;
- target-policy rejection, denied egress, authorization denial, idempotency conflict, rate-limit, and worker-sandbox termination counts;
- artifact/evidence persistence and retention-deletion success/failure.

Metrics labels must not contain raw URLs, selectors, user input, evidence content, or credentials.

### Audit event contract

Audit events are append-only and safe: actor, project, resource/version, action, timestamp, outcome, and categorical reason. Required event families include membership/role, target/version approval, test-version creation, execution/retry/cancel, worker claim/lease/recovery, evidence/report access, retention deletion, and provider configuration opt-in. Audit events do not replace detailed evidence records.

## 12. Failure scenarios and recovery procedures

| Scenario | Detection | Recovery / user-visible outcome |
| --- | --- | --- |
| API request is repeated | Unique idempotency key/fingerprint | Return original execution, not a duplicate job. |
| Two workers race to claim | `FOR UPDATE SKIP LOCKED` and lease update | One worker claims; the other receives no row. |
| Worker crashes or stops heartbeating | Lease expires | Terminate sandbox/browser via the independent deadline/supervisor, mark attempt `lost`, job `recovery_required`, then safely finalize failed; an operator may create a linked retry job. |
| Stale worker reports terminal result | Conditional lease/fencing update affects zero rows | Reject write and terminate/clean up its sandbox; recovery bookkeeping remains authoritative only for the operational loss, not for an inferred browser result. |
| Browser timeout | Worker deadline/action timeout | Bounded cleanup; attempt `timed_out`; deterministic classifier sees only actual sanitized evidence. |
| User cancels while worker runs | `cancel_requested_at` plus worker poll | Stop at safe boundary; terminal `cancelled` or safe cleanup failure. |
| Evidence sanitizer/digest validation fails | Persistence validation | Discard invalid payload; finalize `finalization_failed`/job failed with a safe category if possible, without authoritative evidence or defect conclusion. |
| PostgreSQL outage during finalization | Terminal transaction fails | Roll back; stop browser and report no success. Lease recovery records loss/recovery-required after database recovery; no replacement browser attempt starts automatically. |
| Target policy revoked after queueing | Dispatch-time approval/version check | Do not claim/start; mark job failed/cancelled with safe `target_not_approved` category. |
| Retention delete partially succeeds | Deletion intent plus idempotent reconciliation | Retry deletion; audit actual completion without resurrecting artifact references. |

## 13. MVP implementation sequence

1. Add FastAPI, SQLAlchemy, Alembic, and PostgreSQL only after this design and the security/deployment decisions are approved.
2. Implement identity integration boundary, project/membership authorization, and audited project-scoped repositories with migrations.
3. Implement target/test definition versioning and validation; keep execution limited to local fixtures until egress/worker controls are present.
4. Implement job/attempt schema, idempotency, PostgreSQL claim/lease/heartbeat/cancel/recovery paths, and state-machine tests without browser execution.
5. Build the isolated worker adapter around existing Phase 0 runtime/evidence/classifier components; enforce target/egress policy before enabling external test targets.
6. Persist sanitized evidence/finding/report metadata and history views; keep screenshots/traces disabled.
7. Add pinned browser-worker integration tests, worker-loss tests, cross-project authorization tests, and controlled F01–F06 regression retention.

## 14. MVP acceptance tests

The Phase 1 backend slice is acceptable when it demonstrates:

1. Administrator/operator/reviewer APIs enforce project isolation, including negative cross-project reads and writes.
2. Target/test versions are immutable, approved at dispatch, and bound into the attempt snapshot.
3. Same idempotency key/same request produces one job; same key/different request returns conflict. `UNIQUE (execution_job_id)` and the deferred `latest_attempt_id` foreign key admit exactly one correctly linked attempt per job.
4. Multiple workers claim distinct PostgreSQL jobs, a stale lease cannot heartbeat or finalize an attempt, and a worker role cannot read or write an unclaimed or cross-project attempt.
5. Cancellation, timeout, worker loss, recovery-required state, and manual retry produce the documented transitions: terminal jobs remain terminal, recovery cannot enqueue browser work, and retry lineage is a distinct operator-authorized linear job chain with no sibling retries.
6. Evidence/finding persistence rejects unsafe, invalid, or cross-project data and preserves deterministic digest/evidence membership semantics; an invalid or unavailable terminal payload cannot produce an authoritative finding.
7. A worker runs separately from the API process and cannot execute an unapproved target; network/sandbox controls meet every P0.7 acceptance criterion.
8. Sanitized report/history APIs expose no credentials, raw page content, evaluation ground truth, or another project’s records.
9. The Phase 0 F01–F06 controlled suite remains reproducible in a pinned worker environment; its output is kept separate from product execution history.

## Open questions and Phase 1 blockers

| Question / blocker | Why it matters |
| --- | --- |
| Identity provider and session transport | Required to implement authenticated FastAPI endpoints and audit actor identity. |
| Worker platform, egress proxy, DNS resolver, and sandbox limits | Required before enabling any remote target. |
| PostgreSQL hosting, backups, encryption, and deletion controls | Required before retaining multi-project execution history. |
| Exact controlled target verification process | Required to approve target ownership safely. |
| Rate/quota values and worker capacity | Requires benchmark data; no Phase 0 throughput estimate exists. |
| Artifact storage expansion | Deferred until visual-artifact privacy and lifecycle design is approved. |

## Decision summary

Phase 1 should begin with one FastAPI application, PostgreSQL as both system of record and durable queue, and one separately deployed browser worker type. The design intentionally favors strong transaction boundaries and simple operational topology over early distributed infrastructure. Existing Phase 0 browser/evidence/classifier code is reused only through a worker adapter, behind authenticated project ownership, immutable versioned policies, leases, and the P0.7 network/isolation controls.
