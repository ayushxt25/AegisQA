# Phase 0 experiment record: P0.3A — Structured Browser Observations

## Objective

Prepare the P0.2 bounded execution runtime for future LLM-based planning by producing a small, structured browser observation. This experiment does not add an LLM, infer permissions from a page, or validate autonomous planning.

## Observation schema

`PlaywrightBrowserTool.observe()` returns a `browser` observation containing `url` (credentials, query string, and fragment removed), a 200-character-bounded `title`, the original `result` assertion field, an array of `permitted_elements`, and `permitted_elements_truncated`. Available elements contain `selector`, `permitted_actions`, `tag`, `role`, `accessible_name`, `visible`, and `enabled`; unavailable elements contain only policy-derived identity and `available: false`.

## Limits and security assumptions

- At most 10 permitted elements are emitted, in sorted selector order. Accessible names are capped at 80 characters.
- Inventory derives exclusively from click/fill selectors in `ActionPolicy`; page content never grants permission.
- Labels and text are untrusted, bounded metadata and never expand authority or construct actions.
- The observer never reads input values, cookies, tokens, page-wide HTML, passwords, or hidden fields. Password and hidden controls are omitted even if policy mistakenly includes them.
- Locator, visibility, enabled-state, and metadata errors are contained per element.

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

- Accessibility names are a small DOM-derived approximation, not a full accessibility tree.
- The fixture-specific policy is not a production authorization model.
- P0.3A creates bounded observations only; it does not use LLM planning or demonstrate autonomous behavior.
