"""Project-scoped persistence and membership authorization services."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from backend.database import DatabaseManager
from backend.errors import SafeApiError
from backend.models import MembershipRole, Project, ProjectMembership, User


class ProjectService:
    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    def create(self, actor: User, name: str, slug: str) -> Project:
        try:
            with self._database.session_scope() as session:
                project = Project(name=name, slug=slug, status="active", created_by_user_id=actor.id)
                session.add(project); session.flush()
                session.add(ProjectMembership(project_id=project.id, user_id=actor.id, role=MembershipRole.ADMINISTRATOR.value, created_by_user_id=actor.id))
                session.flush(); return project
        except IntegrityError:
            raise SafeApiError(409, "project_conflict", "Project could not be created.") from None

    def list_projects(self, actor: User, after: Optional[str], limit: int):
        with self._database.session_scope() as session:
            query = select(Project).join(ProjectMembership, ProjectMembership.project_id == Project.id).where(ProjectMembership.user_id == actor.id, ProjectMembership.revoked_at.is_(None)).order_by(Project.id).limit(limit + 1)
            if after is not None: query = query.where(Project.id > after)
            rows = list(session.scalars(query)); return rows[:limit], (str(rows[limit].id) if len(rows) > limit else None)

    def get_project(self, actor: User, project_id: str, administrator: bool = False) -> Project:
        with self._database.session_scope() as session:
            membership = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == actor.id, ProjectMembership.revoked_at.is_(None)))
            if membership is None: raise SafeApiError(404, "not_found", "Resource not found.")
            if administrator and membership.role != MembershipRole.ADMINISTRATOR.value: raise SafeApiError(403, "forbidden", "Permission denied.")
            project = session.get(Project, project_id)
            if project is None: raise SafeApiError(404, "not_found", "Resource not found.")
            return project

    def memberships(self, actor: User, project_id: str):
        self.get_project(actor, project_id, administrator=True)
        with self._database.session_scope() as session:
            return list(session.scalars(select(ProjectMembership).where(ProjectMembership.project_id == project_id).order_by(ProjectMembership.created_at, ProjectMembership.id)))

    def grant(self, actor: User, project_id: str, user_id: str, role: str) -> ProjectMembership:
        return self._mutate(actor, project_id, user_id, role, "grant")

    def change_role(self, actor: User, project_id: str, user_id: str, role: str) -> ProjectMembership:
        if str(actor.id) == user_id: raise SafeApiError(403, "forbidden", "Permission denied.")
        return self._mutate(actor, project_id, user_id, role, "change")

    def revoke(self, actor: User, project_id: str, user_id: str) -> None:
        if str(actor.id) == user_id: raise SafeApiError(403, "forbidden", "Permission denied.")
        with self._database.session_scope() as session:
            self._locked_admin(session, actor, project_id)
            membership = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user_id, ProjectMembership.revoked_at.is_(None)).with_for_update())
            if membership is None: raise SafeApiError(404, "not_found", "Resource not found.")
            self._prevent_last_admin(session, project_id, membership, None)
            membership.revoked_at = datetime.now(timezone.utc)

    def _mutate(self, actor, project_id, user_id, role, operation):
        try:
            with self._database.session_scope() as session:
                self._locked_admin(session, actor, project_id)
                target = session.get(User, user_id)
                if target is None: raise SafeApiError(404, "not_found", "Resource not found.")
                if target.status != "active": raise SafeApiError(409, "user_unavailable", "User cannot receive membership.")
                current = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user_id, ProjectMembership.revoked_at.is_(None)).with_for_update())
                if operation == "grant":
                    if current is not None: raise SafeApiError(409, "membership_conflict", "Membership already exists.")
                else:
                    if current is None: raise SafeApiError(404, "not_found", "Resource not found.")
                    self._prevent_last_admin(session, project_id, current, role)
                    current.revoked_at = datetime.now(timezone.utc); session.flush()
                membership = ProjectMembership(project_id=project_id, user_id=user_id, role=role, created_by_user_id=actor.id)
                session.add(membership); session.flush(); return membership
        except IntegrityError:
            raise SafeApiError(409, "membership_conflict", "Membership could not be changed.") from None

    def _locked_admin(self, session, actor, project_id):
        project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if project is None: raise SafeApiError(404, "not_found", "Resource not found.")
        membership = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == actor.id, ProjectMembership.revoked_at.is_(None)))
        if membership is None: raise SafeApiError(404, "not_found", "Resource not found.")
        if membership.role != MembershipRole.ADMINISTRATOR.value: raise SafeApiError(403, "forbidden", "Permission denied.")

    def _prevent_last_admin(self, session, project_id, membership, next_role):
        if membership.role != MembershipRole.ADMINISTRATOR.value or next_role == MembershipRole.ADMINISTRATOR.value: return
        admins = session.scalars(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.role == MembershipRole.ADMINISTRATOR.value, ProjectMembership.revoked_at.is_(None)).with_for_update()).all()
        if len(admins) <= 1: raise SafeApiError(409, "last_administrator", "Project must retain an administrator.")
