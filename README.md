# AegisQA — Phase 0 feasibility experiments

AegisQA Phase 0 is a set of small, local Python/Playwright experiments for an evidence-driven browser-testing platform. It is not a product service and does not claim autonomous testing, production readiness, or secure hosted browser execution.

## Implemented Phase 0 capabilities

- A deterministic local Chromium experiment that opens a fixture, clicks a button, verifies the expected result, captures a screenshot, records a Playwright trace, measures duration, and closes cleanly.
- A bounded stateful browser-agent loop: observe → propose → validate → execute → observe. It uses typed `navigate`, `click`, `fill`, `inspect`, and `finish` actions, static policy plus runtime-feasibility checks, deterministic assertions, and an action budget.
- Mock planning by default, with an optional bounded Gemini structured-output planner. Model output remains subject to the same action validation and cannot declare success.
- Sanitized, immutable evidence contracts with canonical JSON, SHA-256 modification detection, run-scoped provenance, and bounded observations. The digest does not authenticate capture or prove correctness.
- Deterministic evidence-based failure classification with conservative `INSUFFICIENT_EVIDENCE` behavior when evidence is missing, invalid, contradictory, or untrusted.
- Controlled F01–F06 browser evaluation for missing/disabled targets, incorrect results, local HTTP 503, declared delay profiles, and ambiguous evidence. The verified Windows/Chromium result is documented as a controlled local experiment, not a real-world reliability claim.

## Phase 1 backend foundation

P1.2 extends the FastAPI foundation under `backend/` with a synchronous SQLAlchemy/PostgreSQL engine manager, short-lived transaction-scoped sessions, and Alembic migrations for users, projects, and project memberships. It does **not** implement authentication, project APIs, execution jobs, queues, workers, or browser execution.

There is still no authentication/authorization, durable job queue, browser-worker sandbox, remote-target egress enforcement, Docker Compose configuration, frontend, public execution endpoint, or production artifact store. The optional Gemini experiment is not an MVP product dependency.

Read the design records before treating these experiments as a product architecture:

- [MVP definition](docs/product/mvp-definition.md)
- [Security threat model](docs/architecture/security-threat-model.md)
- [Backend and durable execution design](docs/architecture/backend-execution-design.md)
- [Deployment feasibility](docs/architecture/deployment-feasibility.md)

## Requirements

- Windows, macOS, or Linux
- Python 3.8 or later for the local/mock experiments
- Internet access once to download Playwright's Chromium binary
- Python 3.9 or later only when enabling the optional Gemini SDK

## Setup and P0.1 browser experiment (PowerShell)

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
python experiment.py
```

Generated artifacts are ignored under `artifacts\`:

- `fixture-result.png`
- `trace.zip` — inspect with `python -m playwright show-trace artifacts\trace.zip`

## Run the P1.2 backend foundation (PowerShell)

After the shared setup above:

```powershell
python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

In a second PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

The process emits structured JSON logs and accepts validated P1.2 configuration variables. Core settings are `AEGISQA_ENVIRONMENT` (`development`, `test`, or `production`), `AEGISQA_DEBUG` (`true` or `false`), `AEGISQA_LOG_LEVEL`, `AEGISQA_SERVICE_NAME`, and `AEGISQA_TRUSTED_HOSTS`. `AEGISQA_DEBUG=true` is rejected in production, and wildcard trusted hosts are rejected. Database configuration is documented below; never place database URLs or other secrets in logs, source control, or test output.

## Local PostgreSQL and migrations (P1.2)

P1.2 uses PostgreSQL only; it does not use SQLite as a substitute for schema or migration checks. Create a local database and provide its URL explicitly. Development has no implicit database URL, and production rejects startup without one.

```powershell
$env:AEGISQA_DATABASE_URL = "postgresql+psycopg://aegisqa:replace-with-local-password@127.0.0.1:5432/aegisqa"
python -m alembic upgrade head
python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

Database pool defaults are deliberately small and can be set through `AEGISQA_DATABASE_POOL_SIZE`, `AEGISQA_DATABASE_MAX_OVERFLOW`, `AEGISQA_DATABASE_POOL_TIMEOUT_SECONDS`, and `AEGISQA_DATABASE_CONNECT_TIMEOUT_SECONDS`. Database URLs are never logged. `/health/live` remains process-only; `/health/ready` returns `503` if an explicitly configured database is unavailable.

## OIDC authentication foundation (P1.3-A)

`GET /v1/me` requires a verified RS256 bearer token and a configured PostgreSQL database. Configure all OIDC values together; production requires HTTPS endpoints. Do not log or commit tokens, JWKS responses, or credentials.

```powershell
$env:AEGISQA_OIDC_ISSUER = "https://issuer.example"
$env:AEGISQA_OIDC_AUDIENCE = "aegisqa-api"
$env:AEGISQA_OIDC_JWKS_URL = "https://issuer.example/.well-known/jwks.json"
$env:AEGISQA_OIDC_JWKS_TIMEOUT_SECONDS = "3"
$env:AEGISQA_OIDC_JWKS_CACHE_SECONDS = "300"
```

Health endpoints remain unauthenticated. P1.3-A adds no project, membership, execution, password, refresh-token, or frontend authentication API.

## Project APIs (P1.3-B)

Authenticated callers can create and list projects at `/v1/projects`. Membership management is project-administrator-only. Project, membership, target, and role information is always resolved server-side from the verified OIDC user; clients cannot authorize themselves with request user IDs or roles.

## Durable execution persistence (P1.4-A)

P1.4-A adds PostgreSQL tables and migrations for jobs, one attempt per job, and append-only safe execution events. It does not add execution APIs, workers, browser execution, or automatic retries. Job transitions are `queued → claimed → running → terminal|recovery_required`; attempt transitions are `queued → claimed → starting → running → terminal`. Future worker writes must fence on attempt ID, lease token, active state, and an unexpired database-time lease.

P1.4-B adds authenticated project-scoped submission, status/history, cancellation, and manual-retry APIs. Submit with a non-empty `Idempotency-Key` header and a bounded `1.0.0` orchestration request (`deadline_seconds` is 1–3600). The endpoints are:

- `POST /v1/projects/{project_id}/executions`
- `GET /v1/projects/{project_id}/executions`
- `GET /v1/projects/{project_id}/executions/{job_id}`
- `POST /v1/projects/{project_id}/executions/{job_id}/cancel`
- `POST /v1/projects/{project_id}/executions/{job_id}/retry`

Administrators and operators may submit, cancel, and retry; reviewers may read project-scoped history. Submission and retry atomically write a queued job, its one queued attempt, and safe lifecycle events. A repeated key with the same request returns the original job; a conflicting request returns `409`. Cancellation is idempotent. This milestone still does not execute jobs: a separate worker milestone is required.

## Durable worker (P1.4-C)

The separate PostgreSQL worker claims queued attempts with `FOR UPDATE SKIP LOCKED`, assigns a random lease token, fences heartbeat/state writes on that unexpired token, and sweeps queued deadline expiry or abandoned leases. Run it independently of the API:

```powershell
$env:AEGISQA_WORKER_ID = "local-worker"
$env:AEGISQA_WORKER_POLL_SECONDS = "2"
$env:AEGISQA_WORKER_LEASE_SECONDS = "30"
$env:AEGISQA_WORKER_HEARTBEAT_SECONDS = "10"
python -m backend.worker
```

P1.4-C has no browser executor. A claimed job is safely finalized as `failed` with the internal `executor_unavailable` outcome; it is never marked successful merely by being claimed. Lease tokens are never returned by the API or written to structured logs.

If queued work remains queued, verify the worker has the same explicit PostgreSQL URL as the API and that its deadline has not passed. A worker that loses its lease cannot write again; the sweeper records `lost` / `recovery_required`, and an operator must use the manual retry API. No automatic retry or browser process exists at this stage.

## Approved browser contracts (P1.5-A)

Administrators create pending HTTPS-only target versions at `POST /v1/projects/{project_id}/targets` and explicitly approve/revoke them. Operators create immutable bounded test-definition versions at `/v1/projects/{project_id}/test-definitions`. New public execution submissions must name both `browser_target_version_id` and `test_definition_version_id`; the target must be approved and every allowed navigation must remain inside its approved origin/path prefixes. Revoking a queued bound target deterministically prevents dispatch and the worker finalizes it as `failed`/`target_not_approved`. This is authorization metadata only: browser execution and network isolation remain disabled.

## Child supervisor (P1.5-B)

`backend.supervisor` launches only the fixed `python -m backend.execution_child` entry point with a minimal allowlisted environment and JSON-lines IPC. It enforces startup/runtime/termination deadlines and process-tree termination. The current child is a deterministic no-browser fixture and returns only `executor_unavailable`; it cannot access PostgreSQL or make browser success authoritative.

## Browser containment foundation (P1.5-C1)

Windows supervised children now receive Job Object process-count, job-memory, and hard CPU-rate limits. This is resource containment only. The explicit browser-containment gate remains **closed** on Windows because no enforceable default-deny network or filesystem sandbox exists here; it is also closed on Linux until a rootless container, cgroup, capability, filesystem, and restricted-network deployment is implemented and verified. No browser path may bypass this gate.

For destructive, PostgreSQL-only integration verification, provision a new, empty disposable database separate from development. The suite is opt-in, verifies the live server/database identity for both targets, rejects loopback aliases and default-port variants that identify the same target, and refuses any test database containing user tables or non-system schemas. A name suffix is not evidence of ownership.

## Restricted Docker sandbox (P1.5-C2)

`backend.docker_sandbox` is a separate, fail-closed adapter for a future browser child. It accepts only a locally resolved immutable `sha256:<image-id>` reference, launches only the fixed diagnostic entry point, and uses Docker Linux containers with `--network none`, non-root identity, `no-new-privileges`, all capabilities dropped, a read-only root filesystem, bounded `/tmp`, PID/memory/CPU limits, and Docker's builtin seccomp profile. It never mounts the Docker socket, host directories, or existing containers and never forwards database URLs or lease tokens. It force-removes only generated `aegisqa-sandbox-<id>` containers.

Build and run the explicit local acceptance probe (this creates only the dedicated sandbox image/container):

```powershell
docker build --pull=false --file sandbox\Dockerfile --tag aegisqa-execution-sandbox:local .
$env:AEGISQA_SANDBOX_IMAGE_ID = docker image inspect --format '{{.Id}}' aegisqa-execution-sandbox:local
$env:AEGISQA_RUN_DOCKER_CONTAINMENT_TESTS = "true"
python -m unittest tests.test_docker_sandbox -v
```

The probe checks Docker-reported restrictions plus blocked DNS, external TCP/UDP, host loopback, private-address, and metadata-address connectivity. This is not an approval for Chromium: the local Docker daemon is not verified rootless, no fixture-only egress path exists, and real browser execution remains disabled.

### Controlled fixture network (P1.5-C3)

The development-only fixture probe creates two uniquely named, labeled `--internal` bridge networks. The sandbox and one fixed HTTP fixture join the approved network; an identical unapproved fixture is isolated on the second network. Docker inspection rejects any unexpected approved-network member. The child can use only the fixed alias/path and rejects the fixture's deliberate external redirect before following it. Neither fixture identity, network name, alias, nor endpoint comes from an execution request. This separate HTTP-only development authorization does not weaken the production HTTPS target-approval contract.

```powershell
docker build --pull=false --file sandbox\Dockerfile --tag aegisqa-execution-sandbox:local .
docker build --pull=false --file sandbox\Fixture.Dockerfile --tag aegisqa-controlled-fixture:local sandbox
$env:AEGISQA_SANDBOX_IMAGE_ID = docker image inspect --format '{{.Id}}' aegisqa-execution-sandbox:local
$env:AEGISQA_FIXTURE_IMAGE_ID = docker image inspect --format '{{.Id}}' aegisqa-controlled-fixture:local
$env:AEGISQA_RUN_DOCKER_CONTAINMENT_TESTS = "true"
python -m unittest tests.test_docker_sandbox -v
```

Cleanup removes only containers/networks whose generated names and `com.aegisqa.owner=fixture-boundary` label both match. Docker daemon administrators remain trusted; container networking alone is not a complete defense against a compromised browser and does not itself enable browser execution.

### Controlled Playwright fixture execution (P1.5-C4)

The sandbox image installs Playwright 1.48.0 and its matching Chromium build from the pinned Bookworm base. Its fixed child reuses the Phase 0 `AgentRunner`, `MockPlanner`, `ActionPolicy`, `PlaywrightBrowserTool`, and deterministic assertion path. It accepts only version `1.0` with one of three trusted scenario IDs; it cannot receive URLs, selectors, scripts, credentials, lease tokens, or user commands.

The browser uses the same owned internal fixture network. Docker enforces non-root/no-new-privileges, dropped capabilities, read-only root, seccomp, no host mounts, no published ports, 1 GiB memory, one CPU, 256 PIDs, and 128 MiB bounded `/tmp`/shared memory. Chromium requires that bounded process profile; no Docker control was removed. A child result is accepted only if its bounded versioned schema passes validation; process exit alone is never success.

```powershell
docker build --pull=false --file sandbox\Dockerfile --tag aegisqa-execution-sandbox:playwright .
docker build --pull=false --file sandbox\Fixture.Dockerfile --tag aegisqa-controlled-fixture:local sandbox
$env:AEGISQA_SANDBOX_IMAGE_ID = docker image inspect --format '{{.Id}}' aegisqa-execution-sandbox:playwright
$env:AEGISQA_FIXTURE_IMAGE_ID = docker image inspect --format '{{.Id}}' aegisqa-controlled-fixture:local
$env:AEGISQA_RUN_DOCKER_CONTAINMENT_TESTS = "true"
python -m unittest tests.test_docker_sandbox -v
```

This is limited to controlled local fixtures. It does not connect P1.4 jobs, permit approved public targets, or enable general browser execution.

## Durable fixture-worker dispatch (P1.5-D1)

The worker can opt in to the same fixture-only Docker runtime. A claimed job must retain approved P1.5-A target/test-version bindings and have exactly this bounded orchestration object: `{"mode":"controlled_fixture","scenario_id":"fixture_increment_v1"}` (or the two fixed failure/timeout scenarios). The target is never used as a browser destination. Legacy, malformed, or arbitrary orchestrations finalize as failed without launching Chromium.

```powershell
$env:AEGISQA_FIXTURE_EXECUTION_ENABLED = "true"
$env:AEGISQA_FIXTURE_SANDBOX_IMAGE_ID = docker image inspect --format '{{.Id}}' aegisqa-execution-sandbox:playwright
$env:AEGISQA_FIXTURE_IMAGE_ID = docker image inspect --format '{{.Id}}' aegisqa-controlled-fixture:local
python -m backend.worker
```

This opt-in is rejected in production. The worker retains the lease, heartbeats while monitoring Docker, and maps only a validated `assertion_passed` result to `succeeded`; cancellation/deadline interruption wins over late output. It does not send database URLs, lease tokens, or API credentials to Docker.

## Durable fixture evidence (P1.5-D2)

Migration `20261122_01` adds append-only, attempt-linked sanitized evidence. The parent—not the Docker child—creates finalized Phase 0 `EvidenceRecord` values and SHA-256 digests after validating the bounded result envelope. Success and failed assertions persist observation, action, and assertion records atomically with the fenced terminal transition. Execution detail returns only bounded sanitized evidence summaries to active project members.

```powershell
$env:AEGISQA_DATABASE_URL = "postgresql+psycopg://aegisqa:replace-with-local-password@127.0.0.1:5432/aegisqa"
$env:AEGISQA_TEST_DATABASE_URL = "postgresql+psycopg://aegisqa:replace-with-test-password@127.0.0.1:5432/aegisqa_test"
$env:AEGISQA_RUN_POSTGRES_INTEGRATION_TESTS = "true"
python -m unittest tests.test_database -v
```

The test harness creates one random `aegisqa_it_<uuid>` schema per integration-test class, uses that same schema for Alembic and ORM connections, and drops only that schema during cleanup—even after a failed assertion or migration. It never deletes tables from `public`. Normal explicit migrations continue to use `AEGISQA_DATABASE_URL` and do not require the opt-in flag.

## Deterministic durable failure classification (P1.5-D3)

Migration `20261123_01` adds one immutable, attempt-scoped deterministic classification result. Only committed failed-attempt evidence is reconstructed as digest-verified Phase 0 records. The Phase 0 classifier remains authoritative; missing, malformed, tampered, cross-attempt, or untrusted evidence fails closed to insufficient evidence. Classification is advisory: it never changes execution status, and a failed assertion without an independently trusted assertion contract does not prove an application defect.

The worker's bounded maintenance sweep also recovers failed attempts that have committed evidence but no classification, such as a crash between finalization and the advisory write. Recovery never reruns Chromium or changes the terminal job/attempt/evidence state; the attempt-level unique constraint resolves concurrent recovery safely.

## Bounded agent experiment

Mock planning is the default and uses only the deterministic local fixture:

```powershell
python agent_experiment.py
```

It writes an ignored structured log to `artifacts\agent-execution.json`.

### Optional Gemini planner (P0.3B experiment)

This command makes a real provider call. It is explicit, bounded, and requires Python 3.9+ plus a configured API key. Never place the key in source control or generated evidence.

```powershell
$env:GEMINI_API_KEY = "your-key"
$env:AEGISQA_MODEL = "gemini-2.5-flash-lite"
python agent_experiment.py --planner gemini
```

See [the P0.3B record](docs/phase-0-p0.3b-gemini-planner.md) for provider bounds, costs, and limitations.

## Controlled F01–F06 evaluation

After Playwright setup, run the local controlled browser evaluation:

```powershell
python -m failure_intelligence.browser_evaluation
```

It writes sanitized, ignored outputs below `artifacts\p0.4c\`. The P0.4C documentation records both the verified Windows run and the limitations of the controlled fixture scenarios.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Some browser-dependent tests skip when Playwright/Chromium is unavailable; a skipped test is not a passing browser validation. See the phase records for results actually executed in each environment.
