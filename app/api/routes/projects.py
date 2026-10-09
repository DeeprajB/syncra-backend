from typing import List

from fastapi import APIRouter, Depends, status

from app.models.auth.user import User
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.services import project as project_service
from app.services.auth import sync_current_user

router = APIRouter(tags=["Projects"])


@router.post(
    "/api/v1/workspaces/{workspace_id}/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    description="Workspace owners, admins, and members can create a project. Viewers cannot.",
)
async def create_project(
    workspace_id: str,
    payload: ProjectCreate,
    current_user: User = Depends(sync_current_user),
) -> ProjectResponse:
    return await project_service.create_project(workspace_id, current_user, payload)


@router.get(
    "/api/v1/workspaces/{workspace_id}/projects",
    response_model=List[ProjectResponse],
    summary="List workspace projects",
    description="Returns active projects in the workspace. Any workspace member can list them.",
)
async def list_projects(
    workspace_id: str,
    current_user: User = Depends(sync_current_user),
) -> List[ProjectResponse]:
    return await project_service.list_projects(workspace_id, current_user)


@router.get("/api/v1/projects/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    current_user: User = Depends(sync_current_user),
) -> ProjectResponse:
    return await project_service.get_project(project_id, current_user)


@router.patch("/api/v1/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: User = Depends(sync_current_user),
) -> ProjectResponse:
    return await project_service.update_project(project_id, current_user, payload)


@router.delete(
    "/api/v1/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a project",
    description=(
        "Soft-archives the project by setting archived_at. "
        "Archived projects are omitted from normal listings. "
        "Only the workspace owner or an admin can archive a project."
    ),
)
async def archive_project(
    project_id: str,
    current_user: User = Depends(sync_current_user),
) -> None:
    await project_service.archive_project(project_id, current_user)
