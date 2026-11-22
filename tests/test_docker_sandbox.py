"""Focused Docker containment tests; real Docker test is explicit opt-in."""
from __future__ import annotations

import os
import unittest

from backend.docker_sandbox import DockerExecutionInterrupted, DockerSandbox, DockerSandboxLimits, FixtureExecutionRequest


class DockerSandboxUnitTests(unittest.TestCase):
    def test_rejects_mutable_or_malformed_image_reference(self):
        for image in ("python:3.11-slim", "repo@sha256:" + "a" * 64, "sha256:" + "g" * 64):
            with self.assertRaises(ValueError):
                DockerSandbox(image, docker_binary="docker")

    def test_validates_resource_bounds(self):
        with self.assertRaises(ValueError):
            DockerSandboxLimits(memory_bytes=1)
        with self.assertRaises(ValueError):
            DockerSandboxLimits(pids_limit=1)
        with self.assertRaises(ValueError):
            FixtureExecutionRequest("1.0", "arbitrary_url")


@unittest.skipUnless(os.environ.get("AEGISQA_RUN_DOCKER_CONTAINMENT_TESTS") == "true", "set AEGISQA_RUN_DOCKER_CONTAINMENT_TESTS=true")
class DockerSandboxIntegrationTests(unittest.TestCase):
    def setUp(self):
        image = os.environ.get("AEGISQA_SANDBOX_IMAGE_ID", "")
        if not image:
            self.skipTest("set AEGISQA_SANDBOX_IMAGE_ID to the locally built sha256 image ID")
        self.sandbox = DockerSandbox(image)

    def test_probe_enforces_effective_restrictions_and_secret_isolation(self):
        old_db, old_token = os.environ.get("AEGISQA_DATABASE_URL"), os.environ.get("AEGISQA_LEASE_TOKEN")
        os.environ["AEGISQA_DATABASE_URL"] = "postgresql://secret"
        os.environ["AEGISQA_LEASE_TOKEN"] = "secret-token"
        try:
            result = self.sandbox.run_probe()
        finally:
            if old_db is None:
                os.environ.pop("AEGISQA_DATABASE_URL", None)
            else:
                os.environ["AEGISQA_DATABASE_URL"] = old_db
            if old_token is None:
                os.environ.pop("AEGISQA_LEASE_TOKEN", None)
            else:
                os.environ["AEGISQA_LEASE_TOKEN"] = old_token
        self.assertEqual(65532, result.payload["uid"])
        for key in ("dns_denied", "outbound_tcp_denied", "outbound_udp_denied", "loopback_host_denied", "private_network_denied", "metadata_denied"):
            self.assertTrue(result.payload[key])
        self.assertTrue(result.payload["readonly_root"])

    def test_force_remove_cleans_a_controlled_wait_container(self):
        name = self.sandbox.start_wait()
        self.sandbox.remove(name)
        result = self.sandbox._run(["inspect", name], timeout=5, check=False)
        self.assertNotEqual(0, result.returncode)

    def test_fixture_network_allows_only_the_fixed_fixture_and_cleans_up(self):
        fixture = os.environ.get("AEGISQA_FIXTURE_IMAGE_ID", "")
        if not fixture:
            self.skipTest("set AEGISQA_FIXTURE_IMAGE_ID to the locally built sha256 fixture image ID")
        result = self.sandbox.run_fixture_probe(fixture)
        for key in ("fixture_reachable", "unapproved_fixture_denied", "postgres_denied", "redirect_rejected", "public_tcp_denied", "host_loopback_denied", "docker_host_denied"):
            self.assertTrue(result.payload[key])
        inspect = self.sandbox._run(["inspect", result.container_name], timeout=5, check=False)
        self.assertNotEqual(0, inspect.returncode)
        self.assertEqual("", self.sandbox._run(["ps", "--all", "--filter", "label=com.aegisqa.owner=fixture-boundary", "--format", "{{.Names}}"], timeout=5).stdout.strip())
        self.assertEqual("", self.sandbox._run(["network", "ls", "--filter", "label=com.aegisqa.owner=fixture-boundary", "--format", "{{.Name}}"], timeout=5).stdout.strip())

    def test_playwright_runs_verified_assertion_and_rejects_failed_assertion(self):
        fixture = os.environ.get("AEGISQA_FIXTURE_IMAGE_ID", "")
        if not fixture:
            self.skipTest("set AEGISQA_FIXTURE_IMAGE_ID to the locally built sha256 fixture image ID")
        success = self.sandbox.run_playwright_fixture(fixture, FixtureExecutionRequest("1.0", "fixture_increment_v1", 20.0))
        self.assertEqual("assertion_passed", success.payload["outcome"])
        self.assertTrue(success.payload["assertion_verified"])
        self.assertTrue(success.payload["redirect_blocked"])
        failed = self.sandbox.run_playwright_fixture(fixture, FixtureExecutionRequest("1.0", "fixture_assertion_failure_v1", 20.0))
        self.assertEqual("assertion_failed", failed.payload["outcome"])
        self.assertFalse(failed.payload["assertion_verified"])

    def test_playwright_timeout_removes_owned_resources(self):
        fixture = os.environ.get("AEGISQA_FIXTURE_IMAGE_ID", "")
        if not fixture:
            self.skipTest("set AEGISQA_FIXTURE_IMAGE_ID to the locally built sha256 fixture image ID")
        with self.assertRaises(DockerExecutionInterrupted):
            self.sandbox.run_playwright_fixture(fixture, FixtureExecutionRequest("1.0", "fixture_timeout_v1", 1.0))
        self.assertEqual("", self.sandbox._run(["ps", "--all", "--filter", "label=com.aegisqa.owner=fixture-boundary", "--format", "{{.Names}}"], timeout=5).stdout.strip())
        self.assertEqual("", self.sandbox._run(["network", "ls", "--filter", "label=com.aegisqa.owner=fixture-boundary", "--format", "{{.Name}}"], timeout=5).stdout.strip())

    def test_playwright_cancellation_and_lease_loss_remove_owned_resources(self):
        fixture = os.environ.get("AEGISQA_FIXTURE_IMAGE_ID", "")
        if not fixture:
            self.skipTest("set AEGISQA_FIXTURE_IMAGE_ID to the locally built sha256 fixture image ID")
        for reason, kwargs in (("cancelled", {"cancellation_requested": lambda: True}), ("lease_lost", {"lease_valid": lambda: False})):
            with self.assertRaises(DockerExecutionInterrupted) as interrupted:
                self.sandbox.run_playwright_fixture(fixture, FixtureExecutionRequest("1.0", "fixture_timeout_v1", 10.0), **kwargs)
            self.assertEqual(reason, str(interrupted.exception))
            self.assertEqual("", self.sandbox._run(["ps", "--all", "--filter", "label=com.aegisqa.owner=fixture-boundary", "--format", "{{.Names}}"], timeout=5).stdout.strip())
            self.assertEqual("", self.sandbox._run(["network", "ls", "--filter", "label=com.aegisqa.owner=fixture-boundary", "--format", "{{.Name}}"], timeout=5).stdout.strip())
