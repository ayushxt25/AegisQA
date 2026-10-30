# Phase 0 experiment record: P0.2 — Structured Agent Execution Feasibility

## Objective

Validate execution infrastructure for a small, controlled browser agent loop. This milestone does not validate real AI autonomy, an LLM provider, production task planning, or broad browser testing.

## Architecture

`agent_experiment.py` configures one local-fixture execution. The loop in `agent_runtime/runner.py` is explicit: **observe → propose action → validate → execute → observe**. `MockPlanner` supplies deterministic decisions through the provider-independent `Planner` protocol. `PlaywrightBrowserTool` contains browser calls only. `ExecutionState` owns the objective, action history, observations, remaining budget, status, assertion result, and legal transitions. The assertion evaluator accepts only the original P0.1 success state: `Clicks: 1`.

Validation allowlists the single local fixture URL and its known selectors. It rejects unsupported tools, extra arguments, arbitrary selectors, navigation outside the fixture, and finish actions with arguments. A finish proposal cannot turn an unverified assertion into success. The action budget is constrained to 1–10 and defaults to 10.

## Acceptance criteria

- The deterministic mock planner navigates to the fixture and clicks **Increment**.
- A successful execution records `Clicks: 1`, has status `succeeded`, and writes `artifacts/agent-execution.json`.
- Invalid tools and unauthorized URLs fail before browser execution.
- Execution terminates on verified assertion, failure, or exhausted action budget.
- The focused tests cover successful execution, invalid tools, unauthorized navigation, budget exhaustion, failed assertions, finish without success, and state transitions.

## Verification commands

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m unittest discover -s tests -v
python agent_experiment.py
Get-Content artifacts\agent-execution.json
```

## Known limitations

- The planner is deterministic and does not call an LLM or evaluate untrusted text.
- The browser policy is intentionally fixture-specific, not a general web authorization model.
- The JSON log is local, contains only controlled state fields, and is not a production audit system.
- This experiment does not establish autonomous planning quality, security beyond the allowlist, multi-page workflows, recovery strategies, concurrency, or production reliability.
