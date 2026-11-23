"""Fixed diagnostic workload for the restricted-container acceptance checks."""
from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit


def _tcp_denied(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=0.5)
    except OSError:
        return True
    return False


def _dns_denied() -> bool:
    try:
        socket.getaddrinfo("example.invalid", 443)
    except OSError:
        return True
    return False


def _udp_denied() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        sock.connect(("1.1.1.1", 53))
        sock.send(b"x")
        sock.recv(1)
    except OSError:
        return True
    finally:
        sock.close()
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _fixture_probe() -> dict:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        allowed = opener.open("http://aegisqa-controlled-fixture:8080/allowed", timeout=1).status == 200
    except (OSError, urllib.error.URLError):
        allowed = False
    try:
        opener.open("http://aegisqa-controlled-fixture:8080/redirect", timeout=1)
        redirect_rejected = False
    except urllib.error.HTTPError as error:
        redirect_rejected = error.code in (301, 302, 303, 307, 308) and error.headers.get("Location") == "http://example.invalid/escaped"
    except (OSError, urllib.error.URLError):
        redirect_rejected = False
    return {
        "fixture_reachable": allowed,
        "unapproved_fixture_denied": _dns_forbidden("aegisqa-unapproved-fixture"),
        "postgres_denied": _dns_forbidden("aegisqa-postgres"),
        "redirect_rejected": redirect_rejected,
        "public_tcp_denied": _tcp_denied("1.1.1.1", 443),
        "host_loopback_denied": _tcp_denied("127.0.0.1", 5432),
        "docker_host_denied": _tcp_denied("host.docker.internal", 5432),
    }


_BROWSER_SCENARIOS = ("fixture_increment_v1", "fixture_assertion_failure_v1", "fixture_timeout_v1")
_FIXTURE_URL = "http://aegisqa-controlled-fixture:8080/browser"
_REDIRECT_URL = "http://aegisqa-controlled-fixture:8080/redirect"


def _browser_result(scenario: str) -> dict:
    """Run the existing bounded Phase 0 action/observation/assertion path."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
    from agent_runtime.actions import ActionPolicy
    from agent_runtime.browser import PlaywrightBrowserTool
    from agent_runtime.planner import MockPlanner
    from agent_runtime.runner import AgentRunner
    from agent_runtime.state import ExecutionState

    started = time.monotonic()
    redirect_blocked = False
    stage = "browser_launch"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=10_000)
            try:
                stage = "context_setup"
                context = browser.new_context()
                try:
                    def route_request(route):
                        parsed = urlsplit(route.request.url)
                        if parsed.scheme == "http" and parsed.netloc == "aegisqa-controlled-fixture:8080" and parsed.path in ("/browser", "/redirect"):
                            route.continue_()
                        else:
                            route.abort()

                    context.route("**/*", route_request)
                    page = context.new_page()
                    stage = "redirect_probe"
                    try:
                        page.goto(_REDIRECT_URL, wait_until="load", timeout=2_000)
                    except PlaywrightError:
                        redirect_blocked = True
                    policy = ActionPolicy({_FIXTURE_URL}, {"#increment"}, set(), set())
                    stage = "agent_execution"
                    expected = "Clicks: 1" if scenario != "fixture_assertion_failure_v1" else "Clicks: 2"
                    runner = AgentRunner(
                        MockPlanner(_FIXTURE_URL), PlaywrightBrowserTool(page, policy), policy,
                        lambda observation: observation.data.get("result") == expected,
                    )
                    result = runner.run(ExecutionState("controlled fixture assertion", remaining_action_budget=3)).state
                    if scenario == "fixture_timeout_v1":
                        while True:
                            time.sleep(0.25)
                    observation = result.observations[-1].data if result.observations else {}
                    assertion_verified = result.assertion_result is True
                    return {
                        "type": "browser_result", "schema_version": "1.0", "scenario_id": scenario,
                        "outcome": "assertion_passed" if assertion_verified else "assertion_failed",
                        "assertion_verified": assertion_verified,
                        "safe_error_category": None,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "redirect_blocked": redirect_blocked,
                        "observation": {
                            "url": observation.get("url", ""),
                            "result": observation.get("result"),
                            "permitted_element_count": len(observation.get("permitted_elements", [])),
                        },
                        "evidence_provenance": {
                            "source": "observation", "producing_component": "sandbox.playwright",
                            "captured_at": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception:
        return {
            "type": "browser_result", "schema_version": "1.0", "scenario_id": scenario,
            "outcome": "execution_failed", "assertion_verified": False,
            "safe_error_category": stage,
            "duration_ms": int((time.monotonic() - started) * 1000), "redirect_blocked": redirect_blocked,
            "observation": {"url": "", "result": None, "permitted_element_count": 0},
            "evidence_provenance": {"source": "observation", "producing_component": "sandbox.playwright", "captured_at": datetime.now(timezone.utc).isoformat()},
        }


def _agent_result(raw_request: str) -> dict:
    """Validate the parent packet again before launching Chromium."""
    from backend.agent_contracts import AgentContractError, FixtureAgentRequest, FixtureAgentResult
    from agent_runtime.actions import ActionPolicy
    from agent_runtime.browser import PlaywrightBrowserTool
    from agent_runtime.planner import MockPlanner
    from agent_runtime.runner import AgentRunner
    from agent_runtime.state import ExecutionState, ExecutionStatus
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
    try:
        request = FixtureAgentRequest.from_dict(json.loads(raw_request))
    except (TypeError, ValueError, json.JSONDecodeError, AgentContractError):
        raise SystemExit(2)
    started, redirect_blocked = time.monotonic(), False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=min(10_000, request.deadline_ms))
            try:
                context = browser.new_context()
                try:
                    def route_request(route):
                        parsed = urlsplit(route.request.url)
                        if parsed.scheme == "http" and parsed.netloc == "aegisqa-controlled-fixture:8080" and parsed.path in ("/browser", "/redirect"):
                            route.continue_()
                        else:
                            route.abort()
                    context.route("**/*", route_request)
                    page = context.new_page()
                    try:
                        page.goto(_REDIRECT_URL, wait_until="load", timeout=min(2_000, request.deadline_ms))
                    except PlaywrightError:
                        redirect_blocked = True
                    policy = ActionPolicy(set(request.action_policy["allowed_urls"]), set(request.action_policy["allowed_click_targets"]), set(), set())
                    expected = "Clicks: 1" if request.scenario_id != "fixture_assertion_failure_v1" else "Clicks: 2"
                    state = AgentRunner(MockPlanner(_FIXTURE_URL), PlaywrightBrowserTool(page, policy), policy, lambda observation: observation.data.get("result") == expected).run(ExecutionState("controlled fixture assertion", request.action_budget)).state
                    if request.scenario_id == "fixture_timeout_v1":
                        while True: time.sleep(0.25)
                    # A completed false deterministic assertion is authoritative
                    # over MockPlanner's later finish action.  Runtime failures
                    # before that observation remain automation failures.
                    if state.status is ExecutionStatus.BUDGET_EXHAUSTED:
                        completion, reason = "budget_exhausted", "budget_exhausted"
                    elif state.assertion_result is True:
                        completion, reason = "assertion_passed", None
                    elif state.assertion_result is False:
                        completion, reason = "assertion_failed", "assertion_failed"
                    elif state.status is ExecutionStatus.FAILED and state.error and state.error.startswith("Planner finished"):
                        completion, reason = "finished_without_verified_assertion", "finish_unverified"
                    else:
                        completion, reason = "automation_failed", "execution_failed"
                    observation = state.observations[-1].data if state.observations else {}
                    payload = {"type": "agent_attempt_result", "schema_version": "1.0", "project_id": request.project_id, "execution_job_id": request.execution_job_id, "execution_attempt_id": request.execution_attempt_id, "scenario_id": request.scenario_id, "completion": completion, "assertion_verified": state.assertion_result is True, "action_count": len(state.action_history), "remaining_action_budget": state.remaining_action_budget, "duration_ms": min(request.deadline_ms, int((time.monotonic() - started) * 1000)), "safe_failure_reason": reason, "actions": [{"kind": record.action.kind.value, "outcome": "executed" if record.outcome == "executed" else "finish_requested" if record.outcome == "finish requested" else "failed"} for record in state.action_history], "observation_summary": {"current_url_approved": observation.get("url") == _FIXTURE_URL, "permitted_element_count": len(observation.get("permitted_elements", []))}}
                    FixtureAgentResult.from_dict(payload, request)
                    return payload
                finally: context.close()
            finally: browser.close()
    except Exception:
        payload = {"type": "agent_attempt_result", "schema_version": "1.0", "project_id": request.project_id, "execution_job_id": request.execution_job_id, "execution_attempt_id": request.execution_attempt_id, "scenario_id": request.scenario_id, "completion": "automation_failed", "assertion_verified": False, "action_count": 0, "remaining_action_budget": request.action_budget, "duration_ms": min(request.deadline_ms, int((time.monotonic() - started) * 1000)), "safe_failure_reason": "execution_failed", "actions": [], "observation_summary": {"current_url_approved": False, "permitted_element_count": 0}}
        FixtureAgentResult.from_dict(payload, request)
        return payload


def _dns_forbidden(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 5432)
    except OSError:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("probe", "wait", "fixture_probe", "browser", "agent_browser"), required=True)
    parser.add_argument("--scenario", choices=_BROWSER_SCENARIOS)
    args = parser.parse_args()
    if args.mode == "wait":
        print(json.dumps({"type": "ready", "uid": os.getuid()}), flush=True)
        while True:
            time.sleep(0.25)
    if args.mode == "fixture_probe":
        payload = _fixture_probe()
        payload["type"] = "fixture_probe"
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 0
    if args.mode == "browser":
        if args.scenario not in _BROWSER_SCENARIOS:
            return 2
        print(json.dumps(_browser_result(args.scenario), sort_keys=True), flush=True)
        return 0
    if args.mode == "agent_browser":
        raw_request = os.environ.get("AEGISQA_AGENT_REQUEST")
        if not raw_request:
            return 2
        print(json.dumps(_agent_result(raw_request), sort_keys=True), flush=True)
        return 0
    readonly = False
    try:
        with open("/sandbox-write-probe", "w", encoding="utf-8") as handle:
            handle.write("blocked")
    except OSError:
        readonly = True
    print(
        json.dumps(
            {
                "type": "probe",
                "uid": os.getuid(),
                "dns_denied": _dns_denied(),
                "outbound_tcp_denied": _tcp_denied("1.1.1.1", 53),
                "outbound_udp_denied": _udp_denied(),
                "loopback_host_denied": _tcp_denied("127.0.0.1", 5432),
                "private_network_denied": _tcp_denied("10.0.0.1", 80),
                "metadata_denied": _tcp_denied("169.254.169.254", 80),
                "readonly_root": readonly,
                "has_database_url": bool(os.environ.get("AEGISQA_DATABASE_URL")),
                "has_lease_token": bool(os.environ.get("AEGISQA_LEASE_TOKEN")),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
