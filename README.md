# AegisQA — Phase 0 browser runtime feasibility

This repository is a deliberately small feasibility experiment. It opens a deterministic local HTML fixture in Chromium, clicks one button, verifies the changed text, and saves a screenshot plus Playwright trace. It does not include an application backend, database, frontend, AI agent, or Docker configuration.

## Requirements

- Windows, macOS, or Linux
- Python 3.8 or later
- Internet access once to download Playwright's Chromium binary

## Setup and run (PowerShell)

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
python experiment.py
```

Evidence is written to the ignored `artifacts\` directory:

- `fixture-result.png`
- `trace.zip` (open with `python -m playwright show-trace artifacts\trace.zip`)

## Smoke test

After setup, run:

```powershell
python -m unittest discover -s tests -v
```
