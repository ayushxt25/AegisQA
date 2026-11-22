import os
import time
import unittest
from backend.supervisor import ExecutionSupervisor, SupervisorError, SupervisorLimits, _child_environment


class ExecutionSupervisorTest(unittest.TestCase):
    def test_round_trip_is_not_browser_success(self):
        result = ExecutionSupervisor(SupervisorLimits(startup_seconds=2, runtime_seconds=2)).run(
            {"schema_version": "1.0", "mode": "complete"}, lambda: True, lambda: False
        )
        self.assertEqual("executor_unavailable", result.category)

    def test_cancellation_timeout_and_abnormal_exit(self):
        supervisor = ExecutionSupervisor(SupervisorLimits(startup_seconds=2, runtime_seconds=0.1, termination_seconds=2))
        self.assertEqual("cancelled", supervisor.run({"schema_version": "1.0", "mode": "wait"}, lambda: True, lambda: True).category)
        self.assertEqual("timed_out", supervisor.run({"schema_version": "1.0", "mode": "wait"}, lambda: True, lambda: False).category)
        crash_supervisor = ExecutionSupervisor(SupervisorLimits(startup_seconds=2, runtime_seconds=2, termination_seconds=2))
        self.assertEqual("abnormal_exit", crash_supervisor.run({"schema_version": "1.0", "mode": "crash"}, lambda: True, lambda: False).category)

    def test_packet_rejects_secrets_and_child_environment_is_allowlisted(self):
        with self.assertRaises(SupervisorError):
            ExecutionSupervisor().run({"schema_version": "1.0", "mode": "complete", "lease_token": "secret"}, lambda: True, lambda: False)
        environment = _child_environment()
        self.assertNotIn("AEGISQA_DATABASE_URL", environment)
        self.assertNotIn("GEMINI_API_KEY", environment)

    def test_malformed_oversized_truncated_and_unexpected_ipc_fail_closed(self):
        supervisor = ExecutionSupervisor(SupervisorLimits(startup_seconds=2, runtime_seconds=2))
        for mode in ("malformed", "oversized", "truncated", "unexpected"):
            result = supervisor.run({"schema_version": "1.0", "mode": mode}, lambda: True, lambda: False)
            self.assertEqual("protocol_error", result.category)
            self.assertEqual({}, result.payload)

    def test_late_result_is_rejected_when_lease_becomes_stale(self):
        calls = [True, True, False]
        result = ExecutionSupervisor(SupervisorLimits(startup_seconds=2, runtime_seconds=2)).run(
            {"schema_version": "1.0", "mode": "complete"}, lambda: calls.pop(0) if calls else False, lambda: False
        )
        self.assertEqual("lease_lost", result.category)
        self.assertEqual({}, result.payload)

    def test_windows_process_tree_termination_removes_grandchild(self):
        child_pids = []
        result = ExecutionSupervisor(SupervisorLimits(startup_seconds=2, runtime_seconds=0.2, termination_seconds=2)).run(
            {"schema_version": "1.0", "mode": "spawn_wait"}, lambda: True, lambda: False,
            on_message=lambda message: child_pids.append(message["child_pid"]) if "child_pid" in message else None,
        )
        self.assertEqual("timed_out", result.category)
        self.assertEqual(1, len(child_pids))
        deadline = time.monotonic() + 2
        while _process_exists(child_pids[0]) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(_process_exists(child_pids[0]))


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        handle = ctypes.WinDLL("kernel32", use_last_error=True).OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__": unittest.main()
