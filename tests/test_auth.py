"""Focused P1.3-A OIDC verification tests; all JWKS access is mocked."""

from __future__ import annotations

import json
import time
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import create_app
from backend.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    DisabledAccountError,
    OidcAuthenticator,
    UserProvisioner,
)
from backend.config import BackendSettings
from backend.models import User


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, size: int) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


class _MemorySession:
    def __init__(self, database) -> None:
        self.database = database

    def scalar(self, statement):
        del statement
        return self.database.user

    def add(self, user: User) -> None:
        self.database.user = user

    def flush(self) -> None:
        if self.database.user.id is None:
            self.database.user.id = uuid.uuid4()


class _MemoryDatabase:
    configured = True

    def __init__(self, fail_first_commit: bool = False) -> None:
        self.user = None
        self.calls = 0
        self.fail_first_commit = fail_first_commit

    @contextmanager
    def session_scope(self):
        session = _MemorySession(self)
        try:
            yield session
            self.calls += 1
            if self.fail_first_commit and self.calls == 1:
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError("insert", {}, Exception("duplicate"))
        finally:
            pass


class OidcAuthenticationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
        self.jwks = json.dumps({"keys": [jwk]}).encode("utf-8")
        self.settings = BackendSettings(
            environment="test",
            oidc_issuer="http://issuer.test",
            oidc_audience="aegisqa-api",
            oidc_jwks_url="http://issuer.test/jwks",
            oidc_jwks_cache_seconds=60,
        )

    def _token(self, **overrides: object) -> str:
        claims = {
            "iss": self.settings.oidc_issuer,
            "aud": self.settings.oidc_audience,
            "sub": "subject-1",
            "email": "user@example.test",
            "email_verified": True,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "test-key"})

    def _authenticate(self, token: str):
        with patch("backend.auth.urlopen", return_value=_Response(self.jwks)):
            return OidcAuthenticator(self.settings).authenticate(token)

    def test_valid_rs256_token_returns_immutable_verified_principal(self) -> None:
        principal = self._authenticate(self._token(name="Verified User"))
        self.assertEqual("user@example.test", principal.email)
        self.assertTrue(principal.identity_subject.startswith("oidc:"))
        with self.assertRaises(AttributeError):
            principal.email = "changed@example.test"  # type: ignore[misc]

    def test_invalid_token_cases_are_rejected(self) -> None:
        cases = (
            self._token(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
            self._token(iss="http://wrong.test"),
            self._token(aud="wrong-audience"),
            self._token(sub=""),
            self._token(email_verified=False),
            jwt.encode({"sub": "subject-1"}, "secret", algorithm="HS256", headers={"kid": "test-key"}),
            jwt.encode({"sub": "subject-1"}, key="", algorithm="none", headers={"kid": "test-key"}),
        )
        for token in cases:
            with self.subTest(token_kind=token.split(".")[0]):
                with self.assertRaises(AuthenticationError):
                    self._authenticate(token)
        with self.assertRaises(AuthenticationError):
            self._authenticate("not-a-jwt")

    def test_unknown_kid_and_jwks_failure_are_safe(self) -> None:
        token = jwt.encode(
            {"iss": self.settings.oidc_issuer, "aud": self.settings.oidc_audience, "sub": "x", "email": "x@example.test", "email_verified": True, "exp": time.time() + 60},
            self.private_key,
            algorithm="RS256",
            headers={"kid": "unknown"},
        )
        with self.assertRaises(AuthenticationError):
            self._authenticate(token)
        with patch("backend.auth.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(AuthenticationError):
                OidcAuthenticator(self.settings).authenticate(self._token())

    def test_jwks_is_cached(self) -> None:
        opener = Mock(return_value=_Response(self.jwks))
        authenticator = OidcAuthenticator(self.settings)
        with patch("backend.auth.urlopen", opener):
            authenticator.authenticate(self._token())
            authenticator.authenticate(self._token())
        self.assertEqual(1, opener.call_count)

    def test_user_provisioning_is_idempotent_recovers_once_and_rejects_disabled_users(self) -> None:
        principal = AuthenticatedPrincipal("issuer", "subject", "oidc:key", "user@example.test", "User")
        database = _MemoryDatabase(fail_first_commit=True)
        user = UserProvisioner(database).provision(principal)
        self.assertEqual("oidc:key", user.identity_subject)
        self.assertEqual("active", user.status)
        self.assertEqual(2, database.calls)
        self.assertIs(user, UserProvisioner(database).provision(principal))
        user.status = "disabled"
        with self.assertRaises(DisabledAccountError):
            UserProvisioner(database).provision(principal)

    def test_oidc_configuration_rejects_partial_or_unsafe_production_values(self) -> None:
        with self.assertRaises(ValidationError):
            BackendSettings(environment="test", oidc_issuer="http://issuer.test")
        with self.assertRaises(ValidationError):
            BackendSettings(
                environment="production",
                database_url="postgresql+psycopg://user:password@db.example/aegisqa",
                oidc_issuer="http://issuer.test",
                oidc_audience="aegisqa-api",
                oidc_jwks_url="http://issuer.test/jwks",
            )

    def test_me_response_is_allowlisted_and_auth_failures_are_safe(self) -> None:
        app = create_app(settings=self.settings)
        user = User(id=uuid.uuid4(), identity_subject="oidc:private", email="user@example.test", display_name="User", status="active")
        app.state.authenticator.authenticate = Mock(return_value=Mock())
        app.state.user_provisioner.provision = Mock(return_value=user)
        with TestClient(app) as client:
            unauthenticated = client.get("/v1/me")
            authenticated = client.get("/v1/me", headers={"Authorization": "Bearer token"})
        self.assertEqual(401, unauthenticated.status_code)
        self.assertEqual("authentication_required", unauthenticated.json()["error"]["code"])
        self.assertEqual(200, authenticated.status_code)
        self.assertEqual({"id", "email", "display_name", "status"}, set(authenticated.json()))
        self.assertNotIn("identity_subject", authenticated.text)


if __name__ == "__main__":
    unittest.main()
