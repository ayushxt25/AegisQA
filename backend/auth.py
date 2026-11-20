"""OIDC bearer-token verification and verified-user provisioning."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

import jwt
from fastapi import Depends, Request as FastAPIRequest
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.config import BackendSettings
from backend.database import DatabaseManager
from backend.errors import SafeApiError
from backend.models import User


_bearer = HTTPBearer(auto_error=False)
_MAX_JWKS_BYTES = 65536
_MAX_JWKS_KEYS = 20


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    issuer: str
    subject: str
    identity_subject: str
    email: str
    display_name: str


class AuthenticationError(Exception):
    pass


class DisabledAccountError(Exception):
    pass


class AuthenticationUnavailableError(Exception):
    pass


class JwksCache:
    def __init__(self, settings: BackendSettings) -> None:
        self._settings = settings
        self._keys: Dict[str, Any] = {}
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def key_for(self, kid: str) -> Any:
        with self._lock:
            if time.monotonic() >= self._expires_at:
                self._refresh()
            key = self._keys.get(kid)
            if key is None:
                self._refresh()
                key = self._keys.get(kid)
            if key is None:
                raise AuthenticationError()
            return key

    def _refresh(self) -> None:
        if self._settings.oidc_jwks_url is None:
            raise AuthenticationError()
        try:
            request = Request(self._settings.oidc_jwks_url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=self._settings.oidc_jwks_timeout_seconds) as response:
                raw = response.read(_MAX_JWKS_BYTES + 1)
            if len(raw) > _MAX_JWKS_BYTES:
                raise ValueError()
            payload = json.loads(raw.decode("utf-8"))
            keys = payload.get("keys")
            if not isinstance(keys, list) or len(keys) > _MAX_JWKS_KEYS:
                raise ValueError()
            parsed = {}
            for jwk_data in keys:
                if not isinstance(jwk_data, dict) or jwk_data.get("kty") != "RSA" or jwk_data.get("alg") not in (None, "RS256"):
                    continue
                kid = jwk_data.get("kid")
                if isinstance(kid, str) and kid and len(kid) <= 255:
                    parsed[kid] = jwt.PyJWK.from_dict(jwk_data).key
            if not parsed:
                raise ValueError()
        except Exception:
            raise AuthenticationError() from None
        self._keys = parsed
        self._expires_at = time.monotonic() + self._settings.oidc_jwks_cache_seconds


class OidcAuthenticator:
    def __init__(self, settings: BackendSettings, jwks_cache: Optional[JwksCache] = None) -> None:
        self._settings = settings
        self._jwks = jwks_cache if jwks_cache is not None else JwksCache(settings)

    def authenticate(self, token: str) -> AuthenticatedPrincipal:
        if not self._settings.oidc_configured or len(token) > 16384:
            raise AuthenticationError()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise AuthenticationError()
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid or len(kid) > 255:
                raise AuthenticationError()
            claims = jwt.decode(
                token,
                self._jwks.key_for(kid),
                algorithms=["RS256"],
                audience=self._settings.oidc_audience,
                issuer=self._settings.oidc_issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (AuthenticationError, jwt.PyJWTError, ValueError, TypeError):
            raise AuthenticationError() from None
        return _principal_from_claims(claims, self._settings.oidc_issuer)


def _principal_from_claims(claims: Dict[str, Any], issuer: Optional[str]) -> AuthenticatedPrincipal:
    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(issuer, str) or not isinstance(subject, str) or not subject or len(subject) > 255:
        raise AuthenticationError()
    if claims.get("email_verified") is not True or not isinstance(email, str) or not email.strip() or len(email.strip()) > 320:
        raise AuthenticationError()
    display_name = claims.get("name")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = email
    identity_subject = "oidc:" + hashlib.sha256((issuer + "\x00" + subject).encode("utf-8")).hexdigest()
    return AuthenticatedPrincipal(issuer, subject, identity_subject, email.strip(), display_name.strip()[:255])


class UserProvisioner:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    def provision(self, principal: AuthenticatedPrincipal) -> User:
        if not self._database.configured:
            raise AuthenticationUnavailableError()
        for attempt in range(2):
            try:
                with self._database.session_scope() as session:
                    user = session.scalar(select(User).where(User.identity_subject == principal.identity_subject))
                    if user is None:
                        user = User(
                            identity_subject=principal.identity_subject,
                            email=principal.email,
                            display_name=principal.display_name,
                            status="active",
                        )
                        session.add(user)
                        session.flush()
                    if user.status != "active":
                        raise DisabledAccountError()
                    user.email = principal.email
                    user.display_name = principal.display_name
                    user.last_authenticated_at = datetime.now(timezone.utc)
                    return user
            except IntegrityError:
                if attempt:
                    raise AuthenticationError() from None
        raise AuthenticationError()


def get_current_user(
    request: FastAPIRequest,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise SafeApiError(401, "authentication_required", "Authentication is required.")
    try:
        principal = request.app.state.authenticator.authenticate(credentials.credentials)
        return request.app.state.user_provisioner.provision(principal)
    except DisabledAccountError:
        raise SafeApiError(403, "account_disabled", "Account is not active.") from None
    except AuthenticationUnavailableError:
        raise SafeApiError(503, "authentication_unavailable", "Authentication is temporarily unavailable.") from None
    except AuthenticationError:
        raise SafeApiError(401, "authentication_invalid", "Authentication could not be verified.") from None
