from typing import List

from fastapi import APIRouter, Depends, status

from app.models.auth.user import User
from app.schemas.invitation import InvitationCreated
from app.schemas.workspace import WorkspaceCreate, WorkspaceResponse, WorkspaceUpdate
from app.schemas.workspace_member import WorkspaceMemberResponse, WorkspaceMemberUpdate
from app.services import invitation as invitation_service
from app.services import workspace as workspace_service
from app.services.auth import sync_current_user

router = APIRouter(prefix="/api/v1/workspaces", tags=["Workspaces"])


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    current_user: User = Depends(sync_current_user),
) -> WorkspaceResponse:
    return await workspace_service.create_workspace(current_user, payload)


@router.get("", response_model=List[WorkspaceResponse])
async def list_workspaces(
    current_user: User = Depends(sync_current_user),
) -> List[WorkspaceResponse]:
    return await workspace_service.list_workspaces(current_user)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    current_user: User = Depends(sync_current_user),
) -> WorkspaceResponse:
    return await workspace_service.get_workspace(workspace_id, current_user)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdate,
    current_user: User = Depends(sync_current_user),
) -> WorkspaceResponse:
    return await workspace_service.update_workspace(workspace_id, current_user, payload)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    current_user: User = Depends(sync_current_user),
) -> None:
    await workspace_service.delete_workspace(workspace_id, current_user)


@router.get("/{workspace_id}/members", response_model=List[WorkspaceMemberResponse])
async def list_members(
    workspace_id: str,
    current_user: User = Depends(sync_current_user),
) -> List[WorkspaceMemberResponse]:
    return await workspace_service.list_members(workspace_id, current_user)


@router.patch("/{workspace_id}/members/{member_id}", response_model=WorkspaceMemberResponse)
async def update_member_role(
    workspace_id: str,
    member_id: str,
    payload: WorkspaceMemberUpdate,
    current_user: User = Depends(sync_current_user),
) -> WorkspaceMemberResponse:
    return await workspace_service.update_member_role(
        workspace_id,
        member_id,
        current_user,
        payload,
    )


@router.delete("/{workspace_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    workspace_id: str,
    member_id: str,
    current_user: User = Depends(sync_current_user),
) -> None:
    await workspace_service.remove_member(workspace_id, member_id, current_user)


@router.post("/{workspace_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_workspace(
    workspace_id: str,
    current_user: User = Depends(sync_current_user),
) -> None:
    await workspace_service.leave_workspace(workspace_id, current_user)


@router.post(
    "/{workspace_id}/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an invite link",
    description=(
        "Owners and admins can create a reusable invite link. The raw token is returned "
        "once and only its SHA-256 hash is stored. Joining always grants the member role."
    ),
)
async def create_invitation(
    workspace_id: str,
    current_user: User = Depends(sync_current_user),
) -> InvitationCreated:
    return await invitation_service.create_invitation(workspace_id, current_user)
