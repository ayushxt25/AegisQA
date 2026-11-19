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

For destructive, PostgreSQL-only integration verification, provision a new, empty disposable database separate from development. The suite is opt-in, verifies the live server/database identity for both targets, rejects loopback aliases and default-port variants that identify the same target, and refuses any test database containing user tables or non-system schemas. A name suffix is not evidence of ownership.

```powershell
$env:AEGISQA_DATABASE_URL = "postgresql+psycopg://aegisqa:replace-with-local-password@127.0.0.1:5432/aegisqa"
$env:AEGISQA_TEST_DATABASE_URL = "postgresql+psycopg://aegisqa:replace-with-test-password@127.0.0.1:5432/aegisqa_test"
$env:AEGISQA_RUN_POSTGRES_INTEGRATION_TESTS = "true"
python -m unittest tests.test_database -v
```

The test harness creates one random `aegisqa_it_<uuid>` schema per integration-test class, uses that same schema for Alembic and ORM connections, and drops only that schema during cleanup—even after a failed assertion or migration. It never deletes tables from `public`. Normal explicit migrations continue to use `AEGISQA_DATABASE_URL` and do not require the opt-in flag.

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
