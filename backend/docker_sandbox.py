"""Fail-closed Docker containment adapter for a future browser child.

The trusted worker owns database leases.  This module starts only a fixed
image entry point and never passes worker/database secrets into the container.
It exposes only fixed controlled-fixture browser scenarios, never general browser launch.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from backend.agent_contracts import AgentContractError, FixtureAgentRequest, FixtureAgentResult


_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_NAME = re.compile(r"^aegisqa-sandbox-[0-9a-f]{24}$")
_FIXTURE_NAME = re.compile(r"^aegisqa-fixture-[0-9a-f]{24}$")
_NETWORK_NAME = re.compile(r"^aegisqa-fixture-network-[0-9a-f]{24}$")
_OWNER_LABEL = "com.aegisqa.owner=fixture-boundary"
_FIXTURE_ALIAS = "aegisqa-controlled-fixture"
_UNAPPROVED_ALIAS = "aegisqa-unapproved-fixture"
_BROWSER_SCENARIOS = ("fixture_increment_v1", "fixture_assertion_failure_v1", "fixture_timeout_v1")


class DockerContainmentUnavailable(RuntimeError):
    """Docker or a required enforceable restriction is unavailable."""


class DockerContainmentError(RuntimeError):
    """A controlled sandbox command did not meet its contract."""


class DockerExecutionInterrupted(DockerContainmentError):
    """Trusted parent stopped the container before it returned a result."""


@dataclass(frozen=True)
class DockerSandboxLimits:
    memory_bytes: int = 1_073_741_824
    cpu_count: float = 1.0
    pids_limit: int = 256
    tmpfs_bytes: int = 134_217_728
    shm_bytes: int = 134_217_728

    def __post_init__(self) -> None:
        if not 16_777_216 <= self.memory_bytes <= 4_294_967_296:
            raise ValueError("docker sandbox memory limit is invalid")
        if not 0.1 <= self.cpu_count <= 4.0:
            raise ValueError("docker sandbox CPU limit is invalid")
        if not 2 <= self.pids_limit <= 512:
            raise ValueError("docker sandbox process limit is invalid")
        if not 8_388_608 <= self.tmpfs_bytes <= 536_870_912:
            raise ValueError("docker sandbox tmpfs limit is invalid")
        if not 8_388_608 <= self.shm_bytes <= 536_870_912:
            raise ValueError("docker sandbox shared-memory limit is invalid")


@dataclass(frozen=True)
class DockerSandboxResult:
    container_name: str
    payload: Dict[str, object]
    elapsed_seconds: float


@dataclass(frozen=True)
class FixtureExecutionRequest:
    """Trusted-parent-only contract; it is not a public execution request."""

    schema_version: str
    scenario_id: str
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.schema_version != "1.0" or self.scenario_id not in _BROWSER_SCENARIOS:
            raise ValueError("fixture browser request is invalid")
        if not 1.0 <= self.timeout_seconds <= 30.0:
            raise ValueError("fixture browser timeout is invalid")


class DockerSandbox:
    """Launches fixed non-root diagnostic or controlled-fixture workloads only."""

    def __init__(self, image_id: str, limits: DockerSandboxLimits = DockerSandboxLimits(), docker_binary: Optional[str] = None) -> None:
        if not _IMAGE_ID.fullmatch(image_id):
            raise ValueError("sandbox image must be an immutable sha256 image ID")
        self._image_id = image_id
        self._limits = limits
        self._docker = docker_binary or shutil.which("docker")
        if not self._docker:
            raise DockerContainmentUnavailable("Docker CLI is unavailable")

    def verify(self) -> None:
        """Validate daemon, builtin seccomp, and exact locally-present image."""
        version = self._run(["version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"], timeout=5)
        if version.stdout.strip() != "linux/amd64":
            raise DockerContainmentUnavailable("Docker Linux amd64 engine is required")
        security = self._run(["info", "--format", "{{json .SecurityOptions}}"], timeout=5)
        if "seccomp" not in security.stdout.lower():
            raise DockerContainmentUnavailable("Docker seccomp is unavailable")
        self._verify_image(self._image_id)

    def run_probe(self, timeout_seconds: float = 10.0) -> DockerSandboxResult:
        """Execute only the fixed probe; exit status is not execution success."""
        if not 1.0 <= timeout_seconds <= 60.0:
            raise ValueError("sandbox probe timeout is invalid")
        name = "aegisqa-sandbox-" + uuid.uuid4().hex[:24]
        started = time.monotonic()
        try:
            self.verify()
            self._start(name, "probe")
            self._verify_effective(name)
            waited = self._run(["wait", name], timeout=timeout_seconds)
            if waited.stdout.strip() != "0":
                raise DockerContainmentError("sandbox probe exited abnormally")
            logs = self._run(["logs", "--tail", "1", name], timeout=5)
            payload = _parse_probe(logs.stdout)
            if not (payload["uid"] == 65532 and payload["readonly_root"] and all(payload[key] for key in _NETWORK_PROBES)):
                raise DockerContainmentError("sandbox restrictions were not effective")
            if payload["has_database_url"] or payload["has_lease_token"]:
                raise DockerContainmentError("sandbox received a sensitive parent value")
            return DockerSandboxResult(name, payload, time.monotonic() - started)
        finally:
            self.remove(name)

    def start_wait(self) -> str:
        """Start the fixed wait workload for termination/cleanup verification."""
        name = "aegisqa-sandbox-" + uuid.uuid4().hex[:24]
        self.verify()
        try:
            self._start(name, "wait")
            self._verify_effective(name)
            return name
        except Exception:
            self.remove(name)
            raise

    def run_fixture_probe(self, fixture_image_id: str, timeout_seconds: float = 10.0) -> DockerSandboxResult:
        """Verify fixture-only communication on fresh, owned internal networks."""
        if not _IMAGE_ID.fullmatch(fixture_image_id):
            raise ValueError("fixture image must be an immutable sha256 image ID")
        if not 1.0 <= timeout_seconds <= 60.0:
            raise ValueError("fixture probe timeout is invalid")
        suffix = uuid.uuid4().hex[:24]
        network = "aegisqa-fixture-network-" + suffix
        unapproved_network = "aegisqa-fixture-network-" + uuid.uuid4().hex[:24]
        fixture = "aegisqa-fixture-" + suffix
        unapproved = "aegisqa-fixture-" + uuid.uuid4().hex[:24]
        sandbox = "aegisqa-sandbox-" + suffix
        started = time.monotonic()
        try:
            self.verify()
            self._verify_image(fixture_image_id)
            self._create_internal_network(network)
            self._create_internal_network(unapproved_network)
            self._start_fixture(fixture, fixture_image_id, network, _FIXTURE_ALIAS)
            self._start_fixture(unapproved, fixture_image_id, unapproved_network, _UNAPPROVED_ALIAS)
            self._start(sandbox, "fixture_probe", network=network)
            self._verify_effective(sandbox, network)
            self._verify_network_members(network, (fixture, sandbox))
            waited = self._run(["wait", sandbox], timeout=timeout_seconds)
            if waited.stdout.strip() != "0":
                raise DockerContainmentError("fixture sandbox exited abnormally")
            payload = _parse_fixture_probe(self._run(["logs", "--tail", "1", sandbox], timeout=5).stdout)
            if not all(payload[key] for key in _FIXTURE_PROBES):
                raise DockerContainmentError("fixture-only network restrictions were not effective")
            return DockerSandboxResult(sandbox, payload, time.monotonic() - started)
        finally:
            self.remove(sandbox)
            self._remove_fixture(fixture)
            self._remove_fixture(unapproved)
            self._remove_network(network)
            self._remove_network(unapproved_network)

    def run_playwright_fixture(
        self, fixture_image_id: str, request: FixtureExecutionRequest,
        lease_valid: Optional[Callable[[], bool]] = None,
        cancellation_requested: Optional[Callable[[], bool]] = None,
    ) -> DockerSandboxResult:
        """Run exactly one trusted deterministic browser scenario in the owned topology."""
        if not _IMAGE_ID.fullmatch(fixture_image_id):
            raise ValueError("fixture image must be an immutable sha256 image ID")
        suffix = uuid.uuid4().hex[:24]
        network = "aegisqa-fixture-network-" + suffix
        unapproved_network = "aegisqa-fixture-network-" + uuid.uuid4().hex[:24]
        fixture = "aegisqa-fixture-" + suffix
        unapproved = "aegisqa-fixture-" + uuid.uuid4().hex[:24]
        sandbox = "aegisqa-sandbox-" + suffix
        started = time.monotonic()
        try:
            self.verify()
            self._verify_image(fixture_image_id)
            self._create_internal_network(network)
            self._create_internal_network(unapproved_network)
            self._start_fixture(fixture, fixture_image_id, network, _FIXTURE_ALIAS)
            self._start_fixture(unapproved, fixture_image_id, unapproved_network, _UNAPPROVED_ALIAS)
            self._start(sandbox, "browser", network=network, scenario=request.scenario_id)
            self._verify_effective(sandbox, network)
            self._verify_network_members(network, (fixture, sandbox))
            exit_code = self._wait_for_browser(
                sandbox, request.timeout_seconds, lease_valid, cancellation_requested,
            )
            if exit_code != "0":
                raise DockerContainmentError("browser sandbox exited abnormally")
            payload = _parse_browser_result(self._run(["logs", "--tail", "1", sandbox], timeout=5).stdout, request)
            if payload["outcome"] == "assertion_passed" and not payload["assertion_verified"]:
                raise DockerContainmentError("browser success was not assertion verified")
            return DockerSandboxResult(sandbox, payload, time.monotonic() - started)
        finally:
            self.remove(sandbox)
            self._remove_fixture(fixture)
            self._remove_fixture(unapproved)
            self._remove_network(network)
            self._remove_network(unapproved_network)

    def run_agent_fixture(
        self, fixture_image_id: str, request: FixtureAgentRequest,
        lease_valid: Optional[Callable[[], bool]] = None,
        cancellation_requested: Optional[Callable[[], bool]] = None,
    ) -> DockerSandboxResult:
        """Run the bounded MockPlanner path; request/result remain parent-validated."""
        if not isinstance(request, FixtureAgentRequest) or not _IMAGE_ID.fullmatch(fixture_image_id):
            raise ValueError("fixture agent request or image is invalid")
        try:
            request = FixtureAgentRequest.from_dict(request.to_dict())
        except AgentContractError as error:
            raise DockerContainmentError("fixture agent request is invalid") from error
        suffix = uuid.uuid4().hex[:24]
        network, unapproved_network = "aegisqa-fixture-network-" + suffix, "aegisqa-fixture-network-" + uuid.uuid4().hex[:24]
        fixture, unapproved, sandbox = "aegisqa-fixture-" + suffix, "aegisqa-fixture-" + uuid.uuid4().hex[:24], "aegisqa-sandbox-" + suffix
        started = time.monotonic()
        try:
            self.verify(); self._verify_image(fixture_image_id)
            self._create_internal_network(network); self._create_internal_network(unapproved_network)
            self._start_fixture(fixture, fixture_image_id, network, _FIXTURE_ALIAS)
            self._start_fixture(unapproved, fixture_image_id, unapproved_network, _UNAPPROVED_ALIAS)
            self._start(sandbox, "agent_browser", network=network, agent_request=request)
            self._verify_effective(sandbox, network); self._verify_network_members(network, (fixture, sandbox))
            exit_code = self._wait_for_browser(sandbox, request.deadline_ms / 1000.0, lease_valid, cancellation_requested)
            if exit_code != "0":
                raise DockerContainmentError("agent browser sandbox exited abnormally")
            payload = _parse_agent_result(self._run(["logs", "--tail", "1", sandbox], timeout=5).stdout, request)
            return DockerSandboxResult(sandbox, payload, time.monotonic() - started)
        finally:
            self.remove(sandbox); self._remove_fixture(fixture); self._remove_fixture(unapproved)
            self._remove_network(network); self._remove_network(unapproved_network)

    def _wait_for_browser(self, name: str, timeout_seconds: float, lease_valid, cancellation_requested) -> str:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if lease_valid is not None and not lease_valid():
                raise DockerExecutionInterrupted("lease_lost")
            if cancellation_requested is not None and cancellation_requested():
                raise DockerExecutionInterrupted("cancelled")
            if time.monotonic() >= deadline:
                raise DockerExecutionInterrupted("timed_out")
            state = self._run(["inspect", "--format", "{{.State.Running}}", name], timeout=5, check=False)
            if state.returncode:
                raise DockerContainmentError("browser container disappeared")
            if state.stdout.strip() != "true":
                exit_code = self._run(["inspect", "--format", "{{.State.ExitCode}}", name], timeout=5)
                return exit_code.stdout.strip()
            time.sleep(0.2)

    def remove(self, name: str) -> None:
        """Force-remove only a generated sandbox name; this terminates its tree."""
        if not _NAME.fullmatch(name):
            raise ValueError("refusing to remove a non-sandbox container")
        self._remove_owned_container(name)

    def _start(self, name: str, mode: str, network: str = "none", scenario: Optional[str] = None, agent_request: Optional[FixtureAgentRequest] = None) -> None:
        if mode not in ("probe", "wait") or not _NAME.fullmatch(name):
            if mode not in ("fixture_probe", "browser", "agent_browser") or not _NAME.fullmatch(name) or not _NETWORK_NAME.fullmatch(network):
                raise DockerContainmentError("invalid fixed sandbox workload")
        if mode == "browser" and scenario not in _BROWSER_SCENARIOS:
            raise DockerContainmentError("invalid fixed browser scenario")
        if mode != "browser" and scenario is not None:
            raise DockerContainmentError("unexpected sandbox scenario")
        if (mode == "agent_browser") != (agent_request is not None):
            raise DockerContainmentError("invalid fixed agent workload")
        if network != "none" and not _NETWORK_NAME.fullmatch(network):
            raise DockerContainmentError("invalid fixed sandbox workload")
        args = [
            "run", "--detach", "--name", name,
            "--label", _OWNER_LABEL, "--pull", "never", "--network", network, "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=%d" % self._limits.tmpfs_bytes, "--shm-size", str(self._limits.shm_bytes),
            "--user", "65532:65532", "--security-opt", "no-new-privileges:true",
            "--cap-drop", "ALL", "--pids-limit", str(self._limits.pids_limit),
            "--memory", str(self._limits.memory_bytes), "--cpus", str(self._limits.cpu_count),
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "HOME=/tmp",
        ]
        if agent_request is not None:
            args.extend(["--env", "AEGISQA_AGENT_REQUEST=" + json.dumps(agent_request.to_dict(), sort_keys=True, separators=(",", ":"))])
        args.extend([self._image_id, "--mode", mode])
        if scenario is not None:
            args.extend(["--scenario", scenario])
        self._run(args, timeout=10)

    def _verify_effective(self, name: str, network: str = "none") -> None:
        raw = self._run(["inspect", name], timeout=5).stdout
        try:
            value = json.loads(raw)[0]
            host = value["HostConfig"]
            config = value["Config"]
            security = host.get("SecurityOpt") or []
            cap_drop = host.get("CapDrop") or []
            tmpfs = host.get("Tmpfs") or {}
            valid = (
                config.get("User") == "65532:65532"
                and host.get("NetworkMode") == network
                and host.get("Privileged") is False
                and not host.get("Binds")
                and host.get("PidMode") != "host"
                and host.get("IpcMode") != "host"
                and host.get("UTSMode") != "host"
                and host.get("UsernsMode") != "host"
                and host.get("CgroupnsMode") != "host"
                and host.get("ReadonlyRootfs") is True
                and "ALL" in cap_drop
                and "no-new-privileges:true" in security
                and host.get("PidsLimit") == self._limits.pids_limit
                and host.get("Memory") == self._limits.memory_bytes
                and host.get("NanoCpus") == int(self._limits.cpu_count * 1_000_000_000)
                and host.get("ShmSize") == self._limits.shm_bytes
                and isinstance(tmpfs.get("/tmp"), str)
                and "noexec" in tmpfs["/tmp"]
                and "nosuid" in tmpfs["/tmp"]
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            valid = False
        if not valid:
            raise DockerContainmentError("Docker did not apply required sandbox restrictions")

    def _verify_image(self, image_id: str) -> None:
        inspect = self._run(["image", "inspect", "--format", "{{.Id}}", image_id], timeout=5)
        if inspect.stdout.strip() != image_id:
            raise DockerContainmentUnavailable("required containment image is unavailable")

    def _create_internal_network(self, name: str) -> None:
        if not _NETWORK_NAME.fullmatch(name):
            raise DockerContainmentError("invalid owned network name")
        self._run(["network", "create", "--driver", "bridge", "--internal", "--label", _OWNER_LABEL, name], timeout=10)
        raw = self._run(["network", "inspect", name], timeout=5).stdout
        try:
            value = json.loads(raw)[0]
            valid = value["Name"] == name and value["Driver"] == "bridge" and value["Internal"] is True and value.get("Labels", {}).get("com.aegisqa.owner") == "fixture-boundary"
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            valid = False
        if not valid:
            raise DockerContainmentError("Docker did not apply the owned internal network")

    def _start_fixture(self, name: str, image_id: str, network: str, alias: str) -> None:
        if not (_FIXTURE_NAME.fullmatch(name) and _NETWORK_NAME.fullmatch(network) and alias in (_FIXTURE_ALIAS, _UNAPPROVED_ALIAS)):
            raise DockerContainmentError("invalid controlled fixture")
        args = [
            "run", "--detach", "--name", name, "--label", _OWNER_LABEL, "--pull", "never",
            "--network", network, "--network-alias", alias, "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=%d" % self._limits.tmpfs_bytes, "--shm-size", str(self._limits.shm_bytes),
            "--user", "65532:65532", "--security-opt", "no-new-privileges:true", "--cap-drop", "ALL",
            "--pids-limit", str(self._limits.pids_limit), "--memory", str(self._limits.memory_bytes),
            "--cpus", str(self._limits.cpu_count), image_id,
        ]
        self._run(args, timeout=10)
        self._verify_effective(name, network)

    def _verify_network_members(self, network: str, names) -> None:
        raw = self._run(["network", "inspect", network], timeout=5).stdout
        try:
            containers = json.loads(raw)[0].get("Containers") or {}
            member_names = sorted(item["Name"] for item in containers.values())
            valid = member_names == sorted(names)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            valid = False
        if not valid:
            raise DockerContainmentError("fixture network has unexpected members")

    def _remove_fixture(self, name: str) -> None:
        if not _FIXTURE_NAME.fullmatch(name):
            raise ValueError("refusing to remove a non-fixture container")
        self._remove_owned_container(name)

    def _remove_owned_container(self, name: str) -> None:
        inspected = self._run(["inspect", name], timeout=5, check=False)
        if inspected.returncode:
            return
        try:
            labels = json.loads(inspected.stdout)[0].get("Config", {}).get("Labels", {})
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise DockerContainmentError("refusing to remove an uninspectable container")
        if labels.get("com.aegisqa.owner") != "fixture-boundary":
            raise DockerContainmentError("refusing to remove a non-owned container")
        self._run(["rm", "--force", name], timeout=10, check=False)

    def _remove_network(self, name: str) -> None:
        if not _NETWORK_NAME.fullmatch(name):
            raise ValueError("refusing to remove a non-fixture network")
        inspected = self._run(["network", "inspect", name], timeout=5, check=False)
        if inspected.returncode:
            return
        try:
            labels = json.loads(inspected.stdout)[0].get("Labels", {})
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise DockerContainmentError("refusing to remove an uninspectable network")
        if labels.get("com.aegisqa.owner") != "fixture-boundary":
            raise DockerContainmentError("refusing to remove a non-owned network")
        self._run(["network", "rm", name], timeout=10, check=False)

    def _run(self, args, timeout: float, check: bool = True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                [self._docker] + list(args), shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout,
                env={"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DockerContainmentUnavailable("Docker command is unavailable or timed out") from error
        if check and result.returncode:
            raise DockerContainmentError("Docker sandbox command failed")
        return result


def _parse_probe(output: str) -> Dict[str, object]:
    try:
        value = json.loads(output.strip())
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise DockerContainmentError("sandbox probe returned invalid data") from error
    expected = {"type", "uid", "readonly_root", "has_database_url", "has_lease_token"} | set(_NETWORK_PROBES)
    if not isinstance(value, dict) or set(value) != expected or value.get("type") != "probe":
        raise DockerContainmentError("sandbox probe returned an unexpected message")
    return value


def _parse_fixture_probe(output: str) -> Dict[str, object]:
    try:
        value = json.loads(output.strip())
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise DockerContainmentError("fixture sandbox returned invalid data") from error
    expected = {"type"} | set(_FIXTURE_PROBES)
    if not isinstance(value, dict) or set(value) != expected or value.get("type") != "fixture_probe":
        raise DockerContainmentError("fixture sandbox returned an unexpected message")
    return value


def _parse_browser_result(output: str, request: FixtureExecutionRequest) -> Dict[str, object]:
    try:
        value = json.loads(output.strip())
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise DockerContainmentError("browser sandbox returned invalid data") from error
    expected = {
        "type", "schema_version", "scenario_id", "outcome", "assertion_verified", "safe_error_category", "duration_ms",
        "redirect_blocked", "observation", "evidence_provenance",
    }
    observation = value.get("observation") if isinstance(value, dict) else None
    provenance = value.get("evidence_provenance") if isinstance(value, dict) else None
    valid = (
        isinstance(value, dict) and set(value) == expected and value.get("type") == "browser_result"
        and value.get("schema_version") == request.schema_version and value.get("scenario_id") == request.scenario_id
        and value.get("outcome") in ("assertion_passed", "assertion_failed", "execution_failed")
        and isinstance(value.get("assertion_verified"), bool) and isinstance(value.get("duration_ms"), int)
        and value.get("safe_error_category") in (None, "browser_launch", "context_setup", "redirect_probe", "agent_execution")
        and 0 <= value["duration_ms"] <= int(request.timeout_seconds * 1000)
        and value.get("redirect_blocked") is True
        and isinstance(observation, dict) and set(observation) == {"url", "result", "permitted_element_count"}
        and isinstance(observation.get("url"), str) and len(observation["url"]) <= 200
        and observation.get("result") in (None, "Clicks: 0", "Clicks: 1")
        and isinstance(observation.get("permitted_element_count"), int) and 0 <= observation["permitted_element_count"] <= 10
        and isinstance(provenance, dict) and set(provenance) == {"source", "producing_component", "captured_at"}
        and provenance.get("source") == "observation" and provenance.get("producing_component") == "sandbox.playwright"
        and isinstance(provenance.get("captured_at"), str) and len(provenance["captured_at"]) <= 40
    )
    if not valid:
        raise DockerContainmentError("browser sandbox returned an unexpected message")
    return value


def _parse_agent_result(output: str, request: FixtureAgentRequest) -> Dict[str, object]:
    try:
        value = json.loads(output.strip())
        result = FixtureAgentResult.from_dict(value, request)
    except (TypeError, ValueError, json.JSONDecodeError, AgentContractError) as error:
        raise DockerContainmentError("agent browser sandbox returned an invalid result") from error
    # Parent returns only the validated bounded shape; it is still not durable success.
    return {
        "completion": result.completion, "assertion_verified": result.assertion_verified,
        "action_count": result.action_count, "remaining_action_budget": result.remaining_action_budget,
        "duration_ms": result.duration_ms, "safe_failure_reason": result.safe_failure_reason,
        "actions": list(result.actions), "observation_summary": result.observation_summary,
    }


_NETWORK_PROBES = (
    "dns_denied", "outbound_tcp_denied", "outbound_udp_denied",
    "loopback_host_denied", "private_network_denied", "metadata_denied",
)

_FIXTURE_PROBES = (
    "fixture_reachable", "unapproved_fixture_denied", "postgres_denied",
    "redirect_rejected", "public_tcp_denied", "host_loopback_denied", "docker_host_denied",
)
