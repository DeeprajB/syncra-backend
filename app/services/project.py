from datetime import date
from typing import List, Optional

from fastapi import HTTPException, status

from app.core.time import utc_now
from app.models.auth.user import User
from app.models.project import Project, ProjectStatus
from app.models.workspace_member import MemberRole
from app.repositories import projects as projects_repository
from app.schemas.project import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    project_response,
)
from app.services.workspace_access import require_workspace_admin, require_workspace_member

_PROJECT_CREATOR_ROLES = (MemberRole.OWNER, MemberRole.ADMIN, MemberRole.MEMBER)


def _ensure_date_order(start_date: Optional[date], due_date: Optional[date]) -> None:
    if start_date is not None and due_date is not None and due_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Due date cannot be before the start date",
        )


async def _active_project_or_404(project_id: str) -> Project:
    project = await projects_repository.find_active_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


async def create_project(
    workspace_id: str,
    current_user: User,
    payload: ProjectCreate,
) -> ProjectResponse:
    access = await require_workspace_member(workspace_id, current_user)
    if access.membership.role not in _PROJECT_CREATOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewers cannot create projects",
        )

    _ensure_date_order(payload.start_date, payload.due_date)
    user_id = access.membership.user_id
    now = utc_now()
    project = await projects_repository.insert(
        {
            "workspace_id": access.workspace.id,
            "title": payload.title,
            "description": payload.description,
            "status": payload.status,
            "owner_id": user_id,
            "created_by": user_id,
            "start_date": payload.start_date,
            "due_date": payload.due_date,
            "created_at": now,
            "updated_at": now,
            "archived_at": None,
        }
    )
    return project_response(project)


async def list_projects(workspace_id: str, current_user: User) -> List[ProjectResponse]:
    access = await require_workspace_member(workspace_id, current_user)
    projects = await projects_repository.list_active_by_workspace(access.workspace.id)
    return [project_response(project) for project in projects]


async def get_project(project_id: str, current_user: User) -> ProjectResponse:
    project = await _active_project_or_404(project_id)
    await require_workspace_member(project.workspace_id, current_user)
    return project_response(project)


async def update_project(
    project_id: str,
    current_user: User,
    payload: ProjectUpdate,
) -> ProjectResponse:
    project = await _active_project_or_404(project_id)
    await require_workspace_admin(
        project.workspace_id,
        current_user,
        detail="Only the workspace owner or an admin can update this project",
    )
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    if "status" in changes and changes["status"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Status cannot be null",
        )

    start_date = changes["start_date"] if "start_date" in changes else project.start_date
    due_date = changes["due_date"] if "due_date" in changes else project.due_date
    _ensure_date_order(start_date, due_date)

    if isinstance(changes.get("status"), ProjectStatus):
        changes["status"] = changes["status"].value

    updated = await projects_repository.update(project.id, changes)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project_response(updated)


async def archive_project(project_id: str, current_user: User) -> None:
    project = await _active_project_or_404(project_id)
    await require_workspace_admin(
        project.workspace_id,
        current_user,
        detail="Only the workspace owner or an admin can archive this project",
    )
    # Visibility follows archived_at, matching workspaces. Status stays as it was.
    archived = await projects_repository.archive(project.id, utc_now())
    if not archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
