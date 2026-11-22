"""Approved-target and immutable test-contract persistence."""
from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.database import DatabaseManager
from backend.errors import SafeApiError
from backend.models import BrowserTarget, BrowserTargetVersion, MembershipRole, ProjectMembership, TestDefinition, TestDefinitionVersion, User


class TargetContractService:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    def create_target(self, actor: User, project_id: str, name: str, origin: str, prefixes: list):
        origin, prefixes = normalize_target(origin, prefixes)
        try:
            with self._database.session_scope() as session:
                self._administrator(session, actor, project_id)
                target = BrowserTarget(project_id=project_id, name=name, created_by_user_id=actor.id)
                session.add(target); session.flush()
                version = BrowserTargetVersion(id=uuid.uuid4(), project_id=project_id, browser_target_id=target.id, version=1, origin=origin, path_prefixes=prefixes, created_by_user_id=actor.id)
                session.add(version); session.flush()
                return target, version
        except IntegrityError:
            raise SafeApiError(409, "target_conflict", "Target could not be created.") from None

    def approve_target(self, actor: User, project_id: str, version_id: str):
        return self._set_target_state(actor, project_id, version_id, "approved")

    def revoke_target(self, actor: User, project_id: str, version_id: str):
        return self._set_target_state(actor, project_id, version_id, "revoked")

    def _set_target_state(self, actor, project_id, version_id, state):
        with self._database.session_scope() as session:
            self._administrator(session, actor, project_id)
            version = session.scalar(select(BrowserTargetVersion).where(BrowserTargetVersion.project_id == project_id, BrowserTargetVersion.id == version_id).with_for_update())
            if version is None: raise SafeApiError(404, "not_found", "Resource not found.")
            if state == "approved" and version.approval_state != "pending":
                raise SafeApiError(409, "target_state_conflict", "Target version cannot be approved.")
            if state == "revoked" and version.approval_state == "revoked": return version
            version.approval_state = state
            version.approved_at = datetime.now(timezone.utc) if state == "approved" else version.approved_at
            version.approved_by_user_id = actor.id if state == "approved" else version.approved_by_user_id
            return version

    def create_definition(self, actor: User, project_id: str, name: str, objective: str, policy: dict, assertion: dict):
        policy, assertion = validate_test_contract(objective, policy, assertion)
        try:
            with self._database.session_scope() as session:
                self._operator(session, actor, project_id)
                definition = TestDefinition(project_id=project_id, name=name, created_by_user_id=actor.id)
                session.add(definition); session.flush()
                version = TestDefinitionVersion(id=uuid.uuid4(), project_id=project_id, test_definition_id=definition.id, version=1, objective=objective, action_policy=policy, assertion_contract=assertion, created_by_user_id=actor.id)
                session.add(version); session.flush()
                return definition, version
        except IntegrityError:
            raise SafeApiError(409, "definition_conflict", "Test definition could not be created.") from None

    def add_definition_version(self, actor, project_id, definition_id, objective, policy, assertion):
        policy, assertion = validate_test_contract(objective, policy, assertion)
        with self._database.session_scope() as session:
            self._operator(session, actor, project_id)
            definition = session.scalar(select(TestDefinition).where(TestDefinition.project_id == project_id, TestDefinition.id == definition_id).with_for_update())
            if definition is None: raise SafeApiError(404, "not_found", "Resource not found.")
            version = (session.scalar(select(func.max(TestDefinitionVersion.version)).where(TestDefinitionVersion.test_definition_id == definition.id)) or 0) + 1
            item = TestDefinitionVersion(id=uuid.uuid4(), project_id=project_id, test_definition_id=definition.id, version=version, objective=objective, action_policy=policy, assertion_contract=assertion, created_by_user_id=actor.id)
            session.add(item); session.flush(); return item

    def binding(self, session, project_id: str, target_version_id: str, definition_version_id: str):
        target = session.scalar(select(BrowserTargetVersion).where(BrowserTargetVersion.project_id == project_id, BrowserTargetVersion.id == target_version_id, BrowserTargetVersion.approval_state == "approved"))
        definition = session.scalar(select(TestDefinitionVersion).where(TestDefinitionVersion.project_id == project_id, TestDefinitionVersion.id == definition_version_id))
        if target is None or definition is None:
            raise SafeApiError(409, "execution_contract_unavailable", "Execution contract is unavailable.")
        for url in definition.action_policy["allowed_urls"]:
            if not _url_in_target(url, target.origin, target.path_prefixes):
                raise SafeApiError(422, "validation_error", "Request validation failed.")
        return target, definition

    def _administrator(self, session, actor, project_id):
        membership = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == actor.id, ProjectMembership.revoked_at.is_(None)))
        if membership is None: raise SafeApiError(404, "not_found", "Resource not found.")
        if membership.role != MembershipRole.ADMINISTRATOR.value: raise SafeApiError(403, "forbidden", "Permission denied.")

    def _operator(self, session, actor, project_id):
        membership = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == actor.id, ProjectMembership.revoked_at.is_(None)))
        if membership is None: raise SafeApiError(404, "not_found", "Resource not found.")
        if membership.role not in (MembershipRole.ADMINISTRATOR.value, MembershipRole.OPERATOR.value): raise SafeApiError(403, "forbidden", "Permission denied.")


def normalize_target(origin: str, prefixes: list):
    if not isinstance(origin, str) or not isinstance(prefixes, list) or not 1 <= len(prefixes) <= 20:
        raise SafeApiError(422, "validation_error", "Request validation failed.")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise SafeApiError(422, "validation_error", "Request validation failed.")
    host = parsed.hostname.rstrip(".").lower()
    try: ipaddress.ip_address(host); raise SafeApiError(422, "validation_error", "Request validation failed.")
    except ValueError: pass
    try:
        port = parsed.port
    except ValueError:
        raise SafeApiError(422, "validation_error", "Request validation failed.") from None
    if "*" in host or port not in (None, 443): raise SafeApiError(422, "validation_error", "Request validation failed.")
    normalized = sorted(set(prefixes))
    if not all(isinstance(item, str) and 1 <= len(item) <= 256 and item.startswith("/") and not item.startswith("//") and "\\" not in item and "?" not in item and "#" not in item and "/../" not in item and item != "/.." for item in normalized):
        raise SafeApiError(422, "validation_error", "Request validation failed.")
    return urlunsplit(("https", host, "", "", "")), normalized


def validate_test_contract(objective: str, policy: dict, assertion: dict):
    if not isinstance(objective, str) or not 1 <= len(objective.strip()) <= 1000 or not isinstance(policy, dict) or not isinstance(assertion, dict):
        raise SafeApiError(422, "validation_error", "Request validation failed.")
    keys = ("allowed_urls", "allowed_click_targets", "allowed_fill_targets", "allowed_inspect_targets")
    if set(policy) != set(keys) or set(assertion) != {"kind", "selector", "expected_text"} or assertion.get("kind") != "text_equals":
        raise SafeApiError(422, "validation_error", "Request validation failed.")
    clean = {}
    for key in keys:
        values = policy[key]
        if not isinstance(values, list) or len(values) > 20 or not all(isinstance(value, str) and 1 <= len(value) <= 256 for value in values):
            raise SafeApiError(422, "validation_error", "Request validation failed.")
        clean[key] = sorted(set(values))
    if not clean["allowed_urls"] or not isinstance(assertion["selector"], str) or not isinstance(assertion["expected_text"], str) or len(assertion["selector"]) > 256 or len(assertion["expected_text"]) > 256:
        raise SafeApiError(422, "validation_error", "Request validation failed.")
    return clean, {"kind": "text_equals", "selector": assertion["selector"], "expected_text": assertion["expected_text"]}


def _url_in_target(url, origin, prefixes):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), "", "", "")) == origin and not parsed.query and not parsed.fragment and any(parsed.path.startswith(prefix) for prefix in prefixes)
