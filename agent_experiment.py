"""Run the P0.2 deterministic agent against the local P0.1 fixture."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from agent_runtime.actions import ActionPolicy
from agent_runtime.browser import PlaywrightBrowserTool
from agent_runtime.logging import write_execution_log
from agent_runtime.planner import MockPlanner
from agent_runtime.runner import AgentRunner
from agent_runtime.state import ExecutionState, Observation


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_FIXTURE = PROJECT_ROOT / "fixture" / "index.html"
DEFAULT_ARTIFACTS = PROJECT_ROOT / "artifacts"
DEFAULT_TIMEOUT_MS = 10_000


def fixture_assertion(observation: Observation) -> bool:
    """The P0.1 assertion remains the only success condition."""
    return observation.data.get("result") == "Clicks: 1"


def run_agent_experiment(artifacts_dir: Path = DEFAULT_ARTIFACTS) -> int:
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError:
        print(
            "Agent experiment failed: Playwright is not installed. "
            "Run 'python -m pip install -r requirements.txt' first.",
            file=sys.stderr,
        )
        return 1

    fixture_url = DEFAULT_FIXTURE.resolve().as_uri()
    policy = ActionPolicy(
        allowed_urls={fixture_url},
        allowed_click_targets={"#increment"},
        allowed_fill_targets={"#name"},
        allowed_inspect_targets={"#result", "#name"},
    )
    state = ExecutionState(objective="Verify that the fixture result becomes Clicks: 1")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=DEFAULT_TIMEOUT_MS)
            try:
                context = browser.new_context()
                try:
                    context.set_default_timeout(DEFAULT_TIMEOUT_MS)
                    page = context.new_page()
                    runner = AgentRunner(
                        planner=MockPlanner(fixture_url),
                        browser=PlaywrightBrowserTool(page),
                        policy=policy,
                        assertion_evaluator=fixture_assertion,
                    )
                    result = runner.run(state)
                finally:
                    context.close()
            finally:
                browser.close()
    except PlaywrightError as error:
        print("Agent experiment failed: {0}".format(error), file=sys.stderr)
        return 1

    log_path = artifacts_dir / "agent-execution.json"
    write_execution_log(result, log_path)
    print("Agent execution status: {0}".format(result.state.status.value))
    print("Execution log: {0}".format(log_path))
    return 0 if result.state.assertion_result is True else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS)
    args = parser.parse_args(argv)
    return run_agent_experiment(args.artifacts_dir)


if __name__ == "__main__":
    raise SystemExit(main())
