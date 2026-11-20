"""Authenticated project and membership APIs."""
import base64
from typing import Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from backend.auth import get_current_user
from backend.errors import SafeApiError
from backend.models import User

router = APIRouter(prefix="/v1", tags=["projects"])
Role = Literal["administrator", "operator", "reviewer"]
class ProjectInput(BaseModel): name: str = Field(min_length=1, max_length=255); slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
class MemberInput(BaseModel): user_id: UUID; role: Role
class ProjectOut(BaseModel): id: str; name: str; slug: str; status: str
class MemberOut(BaseModel): id: str; user_id: str; role: str; created_at: object; revoked_at: Optional[object]
class ProjectPage(BaseModel): items: list[ProjectOut]; next_cursor: Optional[str]

def _service(request: Request): return request.app.state.project_service
def _cursor(value: Optional[str]) -> Optional[str]:
    if value is None: return None
    try: return str(UUID(base64.urlsafe_b64decode(value.encode("ascii") + b"==").decode("ascii")))
    except Exception: raise SafeApiError(422, "validation_error", "Request validation failed.") from None
def _encode(value: Optional[str]) -> Optional[str]: return None if value is None else base64.urlsafe_b64encode(value.encode("ascii")).decode("ascii").rstrip("=")
def _project(value) -> ProjectOut: return ProjectOut(id=str(value.id), name=value.name, slug=value.slug, status=value.status)
def _member(value) -> MemberOut: return MemberOut(id=str(value.id), user_id=str(value.user_id), role=value.role, created_at=value.created_at, revoked_at=value.revoked_at)

@router.post("/projects", status_code=201, response_model=ProjectOut)
def create_project(body: ProjectInput, request: Request, user: User = Depends(get_current_user)): return _project(_service(request).create(user, body.name, body.slug))
@router.get("/projects", response_model=ProjectPage)
def list_projects(request: Request, cursor: Optional[str] = None, limit: int = Query(20, ge=1, le=100), user: User = Depends(get_current_user)):
    items, next_cursor = _service(request).list_projects(user, _cursor(cursor), limit); return ProjectPage(items=[_project(item) for item in items], next_cursor=_encode(next_cursor))
@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: UUID, request: Request, user: User = Depends(get_current_user)): return _project(_service(request).get_project(user, str(project_id)))
@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: UUID, request: Request, user: User = Depends(get_current_user)): return [_member(item) for item in _service(request).memberships(user, str(project_id))]
@router.post("/projects/{project_id}/members", status_code=201, response_model=MemberOut)
def grant_member(project_id: UUID, body: MemberInput, request: Request, user: User = Depends(get_current_user)): return _member(_service(request).grant(user, str(project_id), str(body.user_id), body.role))
@router.patch("/projects/{project_id}/members/{user_id}", response_model=MemberOut)
def change_member(project_id: UUID, user_id: UUID, body: MemberInput, request: Request, user: User = Depends(get_current_user)):
    if body.user_id != user_id: raise SafeApiError(422, "validation_error", "Request validation failed.")
    return _member(_service(request).change_role(user, str(project_id), str(user_id), body.role))
@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
def revoke_member(project_id: UUID, user_id: UUID, request: Request, user: User = Depends(get_current_user)):
    _service(request).revoke(user, str(project_id), str(user_id))
