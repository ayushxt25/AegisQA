"""Minimal, deterministic Chromium feasibility experiment for AegisQA Phase 0."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Optional, Sequence

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, sync_playwright


DEFAULT_TIMEOUT_MS = 10_000
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_FIXTURE = PROJECT_ROOT / "fixture" / "index.html"
DEFAULT_ARTIFACTS = PROJECT_ROOT / "artifacts"


class ExperimentError(RuntimeError):
    """Raised when the browser feasibility check cannot complete."""


@dataclass(frozen=True)
class ExperimentResult:
    duration_seconds: float
    screenshot_path: Path
    trace_path: Path


def run_experiment(
    fixture_path: Path = DEFAULT_FIXTURE,
    artifacts_dir: Path = DEFAULT_ARTIFACTS,
    headless: bool = True,
) -> ExperimentResult:
    """Run one local Chromium interaction and save its evidence artifacts."""
    fixture_path = fixture_path.resolve()
    if not fixture_path.is_file():
        raise ExperimentError("Fixture does not exist: {0}".format(fixture_path))

    artifacts_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path = artifacts_dir / "fixture-result.png"
    trace_path = artifacts_dir / "trace.zip"

    for artifact in (screenshot_path, trace_path):
        if artifact.exists():
            artifact.unlink()
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    tracing_started = False
    started_at = perf_counter()

    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    headless=headless, timeout=DEFAULT_TIMEOUT_MS
                )
                context = browser.new_context()
                context.set_default_timeout(DEFAULT_TIMEOUT_MS)
                context.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                tracing_started = True

                page = context.new_page()
                page.goto(fixture_path.as_uri(), wait_until="load")
                page.get_by_role("button", name="Increment").click()
                result = page.locator("#result")
                result.wait_for(state="visible")
                if result.text_content() != "Clicks: 1":
                    raise ExperimentError(
                        "Unexpected fixture result: {0!r}".format(result.text_content())
                    )
                page.screenshot(path=screenshot_path, full_page=True)
            finally:
                if context is not None:
                    if tracing_started:
                        try:
                            context.tracing.stop(path=trace_path)
                        except PlaywrightError as error:
                            print(
                                "Warning: unable to save trace: {0}".format(error),
                                file=sys.stderr,
                            )
                    try:
                        context.close()
                    except PlaywrightError as error:
                        print(
                            "Warning: unable to close context: {0}".format(error),
                            file=sys.stderr,
                        )
                if browser is not None:
                    try:
                        browser.close()
                    except PlaywrightError as error:
                        print(
                            "Warning: unable to close browser: {0}".format(error),
                            file=sys.stderr,
                        )

    except PlaywrightError as error:
        raise ExperimentError("Playwright execution failed: {0}".format(error)) from error

    duration_seconds = perf_counter() - started_at
    if not screenshot_path.is_file() or not trace_path.is_file():
        raise ExperimentError("Expected evidence artifacts were not created.")
    return ExperimentResult(duration_seconds, screenshot_path, trace_path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show Chromium while running.")
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS)
    args = parser.parse_args(argv)

    try:
        result = run_experiment(
            artifacts_dir=args.artifacts_dir, headless=not args.headed
        )
    except ExperimentError as error:
        print("Experiment failed: {0}".format(error), file=sys.stderr)
        return 1

    print("Experiment passed in {0:.3f}s".format(result.duration_seconds))
    print("Screenshot: {0}".format(result.screenshot_path))
    print("Trace: {0}".format(result.trace_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
