"""Focused end-to-end smoke test for the Phase 0 browser experiment."""

import tempfile
import unittest
from pathlib import Path

try:
    import playwright.sync_api  # noqa: F401
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
else:
    PLAYWRIGHT_AVAILABLE = True

if PLAYWRIGHT_AVAILABLE:
    from experiment import DEFAULT_FIXTURE, run_experiment


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright is not installed")
class BrowserExperimentSmokeTest(unittest.TestCase):
    def test_local_fixture_interaction_creates_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = run_experiment(
                fixture_path=DEFAULT_FIXTURE,
                artifacts_dir=Path(temporary_directory),
            )

            self.assertGreater(result.duration_seconds, 0)
            self.assertTrue(result.screenshot_path.is_file())
            self.assertGreater(result.screenshot_path.stat().st_size, 0)
            self.assertTrue(result.trace_path.is_file())
            self.assertGreater(result.trace_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
