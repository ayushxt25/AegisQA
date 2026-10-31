# Phase 0 experiment record: P0.3B — Gemini Planner

## Architecture and model selection

`GeminiPlanner` implements the unchanged `Planner` protocol. It uses the official `google-genai` SDK with `gemini-2.5-flash-lite` by default, selected for a compact, cost-conscious experimental action-planning path. `--planner mock` remains the default; `--planner gemini` explicitly enables provider calls. The planner sends only the objective, latest bounded observation, three recent redacted action outcomes, remaining action budget, and policy-derived action vocabulary.

Each inference request has a 10-second HTTP timeout, one SDK attempt (no retry), a 128-token output limit, and a three-call execution ceiling. The SDK is asked for `application/json` output with a schema requiring exactly one `kind`, `target`, and `value` action object. Parsed actions still pass through the unchanged `AgentRunner` validation policy.

## Environment setup and commands

Gemini mode requires Python 3.9 or newer because the current `google-genai` SDK requires it. Mock mode retains the prior Python 3.8-compatible dependency path. Set the key only in the process environment; it is never printed or written to execution logs.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
$env:GEMINI_API_KEY = "your-key"
$env:AEGISQA_MODEL = "gemini-2.5-flash-lite"
python -m unittest discover -s tests -v
python agent_experiment.py --planner mock
python agent_experiment.py --planner gemini
Get-Content artifacts\agent-execution.json
```

## Acceptance criteria

- Mock execution remains the default and existing assertion semantics are unchanged.
- Gemini mode emits one schema-constrained action per request and cannot exceed three provider calls.
- Missing keys, timeout, rate limit, unavailable model, malformed or empty responses end execution safely.
- Logs contain only model identifier, call count, available token usage, and error categories—never keys or prompts.

## Expected costs and limitations

Cost depends on the account, region, and current Gemini pricing; this experiment bounds each run to three small requests and 128 output tokens but does not guarantee a specific cost. No real model success is claimed until a configured model run completes. This is not a production security boundary, reliability evaluation, or autonomous-agent validation.

## Targeted reliability repair

### Observed failure and root cause

In the first real Gemini attempt, the model proposed `click("#increment")` while the page was `about:blank`. Static `ActionPolicy` correctly authorized the selector, but that authorization did not establish that the current page had been navigated or that the element could be acted on. Playwright therefore waited until its action timeout.

### Design decision and repair

Static authorization remains unchanged. Before any click or fill reaches Playwright, `AgentRunner` now checks the latest policy-derived observation: its sanitized URL must be an approved navigation target, and the target must be present with `available`, `visible`, and `enabled` all true. A failed check records a safe validation error and terminates the run as `failed`; it does not replace the model action, retry, or replan.

### Verification requirements

The automated suite must cover click on `about:blank`, missing, hidden, and disabled targets, an available permitted target, and continued mock-planner success. Run `python -m unittest discover -s tests -v` after dependencies are installed.

## Final Local Verification — 2026-11-01

### Automated Verification

- Test command: `python -m unittest discover -s tests -v`
- Result: 25 tests passed.
- Browser smoke test: Passed.
- Mock planner execution: Succeeded.
- Runtime action feasibility validation: Passed.

### Real Gemini Execution

- Model: gemini-2.5-flash-lite
- Execution status: SUCCEEDED
- Deterministic assertion: TRUE
- Model calls: 2
- Input tokens: 755
- Output tokens: 49
- Total tokens: 804
- Browser actions executed: 2
- Remaining action budget: 8
- Provider errors: 0

### Observed Execution Sequence

1. Browser initialized at `about:blank`.
2. Gemini selected an authorized navigation action.
3. Playwright navigated to the local HTML fixture.
4. Browser observation reported that the Increment button was available, visible, and enabled.
5. Gemini selected the click action targeting `#increment`.
6. Playwright executed the click.
7. Browser observation reported `Clicks: 1`.
8. Deterministic assertion evaluated to true.
9. Agent entered the SUCCEEDED terminal state.

### Debugging History

Initial Gemini integration failed because the provider rejected
`additionalProperties` in the `response_schema` configuration.

The integration was corrected to use `response_json_schema`
with standard JSON Schema syntax.

A subsequent real execution demonstrated that Gemini could
propose an element interaction while the browser was on
`about:blank`.

Runtime feasibility validation was added to reject interactions
that were not executable in the latest observed browser state.

The first execution after adding runtime validation failed safely,
confirming that the unavailable action was rejected before
Playwright execution.

Planning instructions were then strengthened to describe
navigation prerequisites, element availability, and permitted
actions.

The subsequent live execution completed successfully.

### Conclusion

The experiment demonstrated the feasibility of a bounded,
model-driven browser testing loop using Gemini, Playwright,
structured observations, runtime action validation, and
independent deterministic assertions.

This is a single successful controlled execution. It does not
establish statistical reliability or production readiness.

### Known Limitations

- Only one simple local HTML fixture was evaluated.
- No autonomous retry or replanning was implemented.
- Runtime feasibility validation depends on the latest observation.
- Browser state may change between observation and execution.
- Provider errors are categorized but not fully diagnosed.
- The experiment does not establish prompt-injection resistance.
- No durability or worker-crash recovery has been demonstrated.