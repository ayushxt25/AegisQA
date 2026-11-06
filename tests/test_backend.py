"""Focused tests for the Phase 1 FastAPI foundation."""

from __future__ import annotations

import asyncio
import io
import json
import unittest

from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from backend.app import create_app
from backend.config import BackendSettings, ConfigurationError, load_settings


class BackendConfigurationTest(unittest.TestCase):
    def test_safe_defaults_are_development_only(self) -> None:
        settings = load_settings({})
        self.assertEqual("development", settings.environment)
        self.assertFalse(settings.debug)
        self.assertIn("testserver", settings.trusted_hosts)

    def test_rejects_production_debug_and_unsafe_host(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_settings({"AEGISQA_ENVIRONMENT": "production", "AEGISQA_DEBUG": "true"})
        with self.assertRaises(ConfigurationError):
            load_settings({"AEGISQA_TRUSTED_HOSTS": "*"})

    def test_direct_settings_construction_enforces_security_invariants(self) -> None:
        invalid_settings = (
            {"environment": "production", "debug": True},
            {"environment": "unsupported"},
            {"log_level": "TRACE"},
            {"service_name": "Invalid Service Name"},
            {"trusted_hosts": ("*",)},
            {"trusted_hosts": ("localhost", "unsafe/path")},
        )
        for values in invalid_settings:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    BackendSettings(**values)


class BackendApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.log_stream = io.StringIO()
        self.app = create_app(
            settings=BackendSettings(environment="test"), log_stream=self.log_stream
        )

    def test_factory_creates_isolated_app(self) -> None:
        other_app = create_app(settings=BackendSettings(environment="test"))
        self.assertIsNot(self.app, other_app)
        self.assertFalse(self.app.state.runtime.initialized)

    def test_independent_applications_keep_separate_log_streams(self) -> None:
        first_stream = io.StringIO()
        second_stream = io.StringIO()
        first_app = create_app(
            settings=BackendSettings(environment="test"), log_stream=first_stream
        )
        second_app = create_app(
            settings=BackendSettings(environment="test"), log_stream=second_stream
        )

        with TestClient(first_app) as first_client:
            first_client.get("/health/live")
            with TestClient(second_app) as second_client:
                second_client.get("/health/ready")
            first_client.get("/health/ready")

        first_entries = [
            json.loads(line) for line in first_stream.getvalue().splitlines() if line
        ]
        second_entries = [
            json.loads(line) for line in second_stream.getvalue().splitlines() if line
        ]
        first_requests = [
            item for item in first_entries if item.get("event") == "request_completed"
        ]
        second_requests = [
            item for item in second_entries if item.get("event") == "request_completed"
        ]
        self.assertNotEqual(first_app.state.logger.name, second_app.state.logger.name)
        self.assertEqual(2, len(first_requests))
        self.assertEqual(1, len(second_requests))
        self.assertEqual("/health/live", first_requests[0]["route"])
        self.assertEqual("/health/ready", second_requests[0]["route"])

    def test_liveness_and_readiness(self) -> None:
        with TestClient(self.app) as client:
            live = client.get("/health/live")
            ready = client.get("/health/ready")
        self.assertEqual(200, live.status_code)
        self.assertEqual({"status": "live"}, live.json())
        self.assertEqual(200, ready.status_code)
        self.assertEqual({"status": "ready"}, ready.json())
        self.assertTrue(self.app.state.runtime.shutdown_complete)

    def test_initialization_failure_keeps_liveness_and_reports_not_ready(self) -> None:
        def fail_startup() -> None:
            raise RuntimeError("password=should-not-be-logged")

        app = create_app(
            settings=BackendSettings(environment="test"),
            initializer=fail_startup,
            log_stream=self.log_stream,
        )
        with TestClient(app) as client:
            live = client.get("/health/live")
            ready = client.get("/health/ready")
        self.assertEqual(200, live.status_code)
        self.assertEqual(503, ready.status_code)
        self.assertEqual("not_ready", ready.json()["error"]["code"])
        self.assertNotIn("password", self.log_stream.getvalue())

    def test_safe_404_and_405_contracts(self) -> None:
        with TestClient(self.app) as client:
            missing = client.get("/missing?token=not-for-logs")
            not_allowed = client.post("/health/live")
        self.assertEqual(404, missing.status_code)
        self.assertEqual("not_found", missing.json()["error"]["code"])
        self.assertEqual(405, not_allowed.status_code)
        self.assertEqual("method_not_allowed", not_allowed.json()["error"]["code"])

    def test_validation_handler_uses_safe_contract(self) -> None:
        scope = {"type": "http", "method": "GET", "headers": []}
        request = Request(scope)
        request.state.request_id = "test-request-id"
        errors = RequestValidationError([])

        async def invoke_handler():
            for handler_type, handler in self.app.exception_handlers.items():
                if handler_type is RequestValidationError:
                    return await handler(request, errors)
            self.fail("Request validation handler was not registered")

        response = asyncio.run(invoke_handler())
        self.assertEqual(422, response.status_code)
        self.assertIn(b"validation_error", response.body)
        self.assertNotIn(b"loc", response.body)

    def test_unexpected_error_handler_is_sanitized(self) -> None:
        @self.app.get("/_test/unexpected-error", include_in_schema=False)
        async def unexpected_error() -> None:
            raise RuntimeError("secret-error-value")

        with TestClient(self.app, raise_server_exceptions=False) as client:
            response = client.get("/_test/unexpected-error")
        self.assertEqual(500, response.status_code)
        self.assertEqual("internal_error", response.json()["error"]["code"])
        self.assertIn(b"internal_error", response.content)
        self.assertNotIn(b"secret-error-value", response.content)
        self.assertNotIn("secret-error-value", self.log_stream.getvalue())

    def test_correlation_logging_excludes_request_secrets(self) -> None:
        request_id = "a1e12e30-315e-4f9d-a35b-a65636906641"
        secret = "super-secret-value"
        with TestClient(self.app) as client:
            response = client.get(
                "/health/live?token={0}".format(secret),
                headers={
                    "X-Request-ID": request_id,
                    "Authorization": "Bearer {0}".format(secret),
                    "Cookie": "session={0}".format(secret),
                },
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual(request_id, response.headers["X-Request-ID"])
        entries = [
            json.loads(line)
            for line in self.log_stream.getvalue().splitlines()
            if line.strip()
        ]
        request_entries = [entry for entry in entries if entry.get("event") == "request_completed"]
        self.assertEqual(1, len(request_entries))
        entry = request_entries[0]
        self.assertEqual("GET", entry["method"])
        self.assertEqual("/health/live", entry["route"])
        self.assertEqual(request_id, entry["request_id"])
        self.assertEqual(200, entry["status_code"])
        self.assertNotIn(secret, self.log_stream.getvalue())
        self.assertNotIn("Authorization", self.log_stream.getvalue())
        self.assertNotIn("Cookie", self.log_stream.getvalue())


if __name__ == "__main__":
    unittest.main()
