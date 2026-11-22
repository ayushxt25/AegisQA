"""Project-scoped approved targets and immutable test contracts."""
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from backend.auth import get_current_user
from backend.models import User

router = APIRouter(prefix="/v1", tags=["browser-contracts"])

class TargetInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    origin: str = Field(min_length=1, max_length=512)
    path_prefixes: list[str] = Field(min_length=1, max_length=20)
class DefinitionInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=1000)
    action_policy: dict[str, Any]
    assertion_contract: dict[str, Any]
class VersionInput(BaseModel):
    objective: str = Field(min_length=1, max_length=1000)
    action_policy: dict[str, Any]
    assertion_contract: dict[str, Any]
def _service(request): return request.app.state.target_contract_service
def _target(value): return {"id": str(value.id), "version": value.version, "origin": value.origin, "path_prefixes": value.path_prefixes, "approval_state": value.approval_state}
def _definition(value): return {"id": str(value.id), "definition_id": str(value.test_definition_id), "version": value.version, "objective": value.objective, "action_policy": value.action_policy, "assertion_contract": value.assertion_contract}

@router.post("/projects/{project_id}/targets", status_code=201)
def create_target(project_id: UUID, body: TargetInput, request: Request, user: User = Depends(get_current_user)):
    target, version = _service(request).create_target(user, str(project_id), body.name, body.origin, body.path_prefixes)
    return {"target_id": str(target.id), "version": _target(version)}
@router.post("/projects/{project_id}/target-versions/{version_id}/approve")
def approve_target(project_id: UUID, version_id: UUID, request: Request, user: User = Depends(get_current_user)): return _target(_service(request).approve_target(user, str(project_id), str(version_id)))
@router.post("/projects/{project_id}/target-versions/{version_id}/revoke")
def revoke_target(project_id: UUID, version_id: UUID, request: Request, user: User = Depends(get_current_user)): return _target(_service(request).revoke_target(user, str(project_id), str(version_id)))
@router.post("/projects/{project_id}/test-definitions", status_code=201)
def create_definition(project_id: UUID, body: DefinitionInput, request: Request, user: User = Depends(get_current_user)):
    definition, version = _service(request).create_definition(user, str(project_id), body.name, body.objective, body.action_policy, body.assertion_contract)
    return {"definition_id": str(definition.id), "version": _definition(version)}
@router.post("/projects/{project_id}/test-definitions/{definition_id}/versions", status_code=201)
def add_definition_version(project_id: UUID, definition_id: UUID, body: VersionInput, request: Request, user: User = Depends(get_current_user)): return _definition(_service(request).add_definition_version(user, str(project_id), str(definition_id), body.objective, body.action_policy, body.assertion_contract))
