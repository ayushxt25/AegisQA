import unittest
from backend.containment import ContainmentCapability, ContainmentUnavailableError, inspect_capability, require_browser_containment
from backend.supervisor import SupervisorLimits


class ContainmentTest(unittest.TestCase):
    def test_current_platform_fails_closed_for_browser(self):
        capability = inspect_capability()
        self.assertFalse(capability.browser_ready)
        with self.assertRaises(ContainmentUnavailableError):
            require_browser_containment()

    def test_unsupported_platform_is_fail_closed(self):
        capability = inspect_capability("unsupported")
        self.assertFalse(capability.browser_ready)
        self.assertFalse(capability.process_tree)

    def test_resource_limit_bounds_are_validated(self):
        with self.assertRaises(ValueError): SupervisorLimits(memory_bytes=1)
        with self.assertRaises(ValueError): SupervisorLimits(process_limit=1)
        with self.assertRaises(ValueError): SupervisorLimits(cpu_rate_percent=101)


if __name__ == "__main__": unittest.main()
