"""Cross-platform supervisor for a non-privileged execution child process."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


PROTOCOL_VERSION = "1.0"
MAX_IPC_BYTES = 8192
MAX_STDERR_BYTES = 4096


class SupervisorError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupervisorLimits:
    startup_seconds: float = 5.0
    runtime_seconds: float = 30.0
    termination_seconds: float = 5.0
    max_children: int = 1
    require_os_resource_containment: bool = False
    memory_bytes: int = 268_435_456
    process_limit: int = 16
    cpu_rate_percent: int = 50

    def __post_init__(self) -> None:
        if not 0.1 <= self.startup_seconds <= 60 or not 0.1 <= self.runtime_seconds <= 3600 or not 0.1 <= self.termination_seconds <= 60 or not 1 <= self.max_children <= 32 or not 16_777_216 <= self.memory_bytes <= 4_294_967_296 or not 2 <= self.process_limit <= 128 or not 1 <= self.cpu_rate_percent <= 100:
            raise ValueError("supervisor limits are invalid")


@dataclass(frozen=True)
class ChildResult:
    category: str
    payload: dict
    elapsed_seconds: float


class ExecutionSupervisor:
    """Owns child lifetime only; the durable worker remains the lease owner."""

    def __init__(self, limits: SupervisorLimits = SupervisorLimits()) -> None:
        if limits.require_os_resource_containment:
            # Process groups terminate trees, but are not memory/CPU containment.
            raise SupervisorError("OS resource containment is not configured")
        self._limits = limits
        self._slots = threading.BoundedSemaphore(limits.max_children)
        self._jobs = {}

    def require_browser_containment(self):
        """Mandatory gate for a future browser-launching code path."""
        from backend.containment import require_browser_containment
        return require_browser_containment()

    def run(
        self,
        packet: dict,
        lease_valid: Callable[[], bool],
        cancellation_requested: Callable[[], bool],
        on_message: Optional[Callable[[dict], None]] = None,
    ) -> ChildResult:
        """Launch one fixed child module and return only a validated result."""

        _validate_packet(packet)
        if not self._slots.acquire(blocking=False):
            raise SupervisorError("worker child capacity is exhausted")
        process = None
        messages = None
        errors = None
        diagnostic_descendants = []
        try:
            process = self._launch()
            started = time.monotonic()
            self._send(process, {"protocol_version": PROTOCOL_VERSION, "type": "start", "packet": packet})
            messages = _Reader(process.stdout)
            errors = _Reader(process.stderr, limit=MAX_STDERR_BYTES)
            ready_deadline = started + self._limits.startup_seconds
            runtime_deadline = started + self._limits.runtime_seconds
            ready = False
            while True:
                now = time.monotonic()
                if not lease_valid():
                    self._terminate(process, diagnostic_descendants)
                    return ChildResult("lease_lost", {}, now - started)
                if cancellation_requested():
                    self._terminate(process, diagnostic_descendants)
                    return ChildResult("cancelled", {}, now - started)
                if now >= runtime_deadline or (not ready and now >= ready_deadline):
                    self._terminate(process, diagnostic_descendants)
                    return ChildResult("timed_out", {}, now - started)
                try:
                    line = messages.get(timeout=min(0.1, runtime_deadline - now))
                except queue.Empty:
                    if process.poll() is not None:
                        return ChildResult("abnormal_exit", {}, time.monotonic() - started)
                    continue
                try:
                    message = _decode_message(line)
                except SupervisorError:
                    self._terminate(process, diagnostic_descendants)
                    return ChildResult("protocol_error", {}, time.monotonic() - started)
                if on_message is not None:
                    on_message(message)
                if "child_pid" in message:
                    if packet["mode"] != "spawn_wait":
                        self._terminate(process, diagnostic_descendants)
                        return ChildResult("protocol_error", {}, time.monotonic() - started)
                    diagnostic_descendants.append(message["child_pid"])
                if message["type"] == "ready":
                    if ready:
                        self._terminate(process, diagnostic_descendants)
                        return ChildResult("protocol_error", {}, time.monotonic() - started)
                    ready = True
                    continue
                if message["type"] == "heartbeat":
                    if not ready:
                        self._terminate(process, diagnostic_descendants)
                        return ChildResult("protocol_error", {}, time.monotonic() - started)
                    continue
                if message["type"] == "result":
                    if not ready:
                        self._terminate(process, diagnostic_descendants)
                        return ChildResult("protocol_error", {}, time.monotonic() - started)
                    if not lease_valid():
                        self._terminate(process, diagnostic_descendants)
                        return ChildResult("lease_lost", {}, time.monotonic() - started)
                    try:
                        category = _result_category(message)
                    except SupervisorError:
                        self._terminate(process, diagnostic_descendants)
                        return ChildResult("protocol_error", {}, time.monotonic() - started)
                    self._terminate(process, diagnostic_descendants)
                    return ChildResult(category, message["result"], time.monotonic() - started)
                self._terminate(process, diagnostic_descendants)
                return ChildResult("protocol_error", {}, time.monotonic() - started)
        finally:
            if process is not None:
                self._terminate(process, diagnostic_descendants)
                for reader in (messages, errors):
                    if reader is not None:
                        reader.close(self._limits.termination_seconds)
                if process.stdin is not None:
                    process.stdin.close()
            self._slots.release()

    def _launch(self):
        options = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
            "shell": False,
            "env": _child_environment(),
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        process = subprocess.Popen([sys.executable, "-m", "backend.execution_child"], **options)
        if os.name == "nt":
            try:
                self._jobs[process.pid] = _assign_windows_job(process, self._limits)
            except Exception:
                process.kill(); process.wait()
                raise SupervisorError("Windows process-tree containment is unavailable") from None
        return process

    def _send(self, process, value: dict) -> None:
        encoded = _encode_message(value)
        try:
            process.stdin.write(encoded + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise SupervisorError("child start channel failed") from None

    def _terminate(self, process, diagnostic_descendants=()) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                job = self._jobs.pop(process.pid, None)
                if job is None:
                    # A Windows child without an assigned Job Object is not
                    # safely supervisable: killing only its parent is unsafe.
                    process.kill()
                    raise SupervisorError("Windows process-tree containment is unavailable")
                _close_windows_handle(job)
                for pid in diagnostic_descendants:
                    _terminate_windows_process(pid)
            else:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=self._limits.termination_seconds)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        finally:
            try: process.wait(timeout=self._limits.termination_seconds)
            except subprocess.TimeoutExpired: pass


class _Reader:
    def __init__(self, stream, limit: int = MAX_IPC_BYTES) -> None:
        self._queue = queue.Queue()
        self._stream = stream
        def read() -> None:
            for line in iter(stream.readline, ""):
                self._queue.put(line[: limit + 1])
        self._thread = threading.Thread(target=read, daemon=True)
        self._thread.start()
    def get(self, timeout: float) -> str:
        return self._queue.get(timeout=timeout)
    def close(self, timeout: float) -> None:
        self._thread.join(timeout=timeout)
        if not self._thread.is_alive():
            self._stream.close()


def _child_environment() -> dict:
    allowed = {"PYTHONUNBUFFERED": "1", "PYTHONPATH": os.getcwd()}
    for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL"):
        if key in os.environ: allowed[key] = os.environ[key]
    return allowed


def _validate_packet(packet: dict) -> None:
    if not isinstance(packet, dict) or packet.get("schema_version") != PROTOCOL_VERSION or set(packet) - {"schema_version", "mode"} or packet.get("mode") not in {"complete", "wait", "crash", "malformed", "oversized", "truncated", "unexpected", "spawn_wait"}:
        raise SupervisorError("child packet is invalid")
    _encode_message(packet)


def _encode_message(value: dict) -> str:
    try: encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError): raise SupervisorError("IPC message is invalid") from None
    if len(encoded.encode("utf-8")) > MAX_IPC_BYTES: raise SupervisorError("IPC message is too large")
    return encoded


def _decode_message(line: str) -> dict:
    if not isinstance(line, str) or len(line.encode("utf-8")) > MAX_IPC_BYTES: raise SupervisorError("child IPC message is too large")
    try: message = json.loads(line)
    except (TypeError, ValueError): raise SupervisorError("child IPC message is malformed") from None
    if not isinstance(message, dict) or message.get("protocol_version") != PROTOCOL_VERSION or message.get("type") not in {"ready", "heartbeat", "result"}: raise SupervisorError("child IPC schema is invalid")
    kind = message["type"]
    if kind == "ready":
        allowed = {"protocol_version", "type", "child_pid"}
        if set(message) - allowed or ("child_pid" in message and (not isinstance(message["child_pid"], int) or message["child_pid"] <= 0)): raise SupervisorError("child IPC schema is invalid")
    elif kind == "heartbeat" and set(message) != {"protocol_version", "type"}: raise SupervisorError("child IPC schema is invalid")
    elif kind == "result" and set(message) != {"protocol_version", "type", "result"}: raise SupervisorError("child IPC schema is invalid")
    return message


def _result_category(message: dict) -> str:
    result = message.get("result")
    if not isinstance(result, dict) or set(result) != {"outcome"} or result["outcome"] != "executor_unavailable":
        raise SupervisorError("child result is invalid")
    return "executor_unavailable"


def _assign_windows_job(process, limits: SupervisorLimits) -> int:
    """Assign before sending work; closing the job kills every descendant."""
    if os.name != "nt":
        raise RuntimeError("not Windows")
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong), ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong), ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong)]
    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle: raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
    info = EXTENDED(); info.BasicLimitInformation.LimitFlags = 0x00002000 | 0x00000008 | 0x00000200  # KILL_ON_CLOSE | ACTIVE_PROCESS | JOB_MEMORY
    info.BasicLimitInformation.ActiveProcessLimit = limits.process_limit
    info.JobMemoryLimit = limits.memory_bytes
    if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(handle); raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
    if not kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(process._handle)):
        kernel32.CloseHandle(handle); raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
    class CPU(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]
    cpu = CPU(0x00000001 | 0x00000004, limits.cpu_rate_percent * 100)
    if not kernel32.SetInformationJobObject(handle, 15, ctypes.byref(cpu), ctypes.sizeof(cpu)):
        kernel32.CloseHandle(handle); raise OSError(ctypes.get_last_error(), "SetInformationJobObject CPU")
    return int(handle)


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    # Explicit termination is required before close; KILL_ON_JOB_CLOSE remains
    # the crash-path backstop if this process exits unexpectedly.
    if not kernel32.TerminateJobObject(wintypes.HANDLE(handle), 1):
        raise OSError(ctypes.get_last_error(), "TerminateJobObject")
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def _terminate_windows_process(pid: int) -> None:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if handle:
        kernel32.TerminateProcess(wintypes.HANDLE(handle), 1)
        kernel32.CloseHandle(wintypes.HANDLE(handle))
