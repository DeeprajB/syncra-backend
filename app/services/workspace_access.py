from typing import NamedTuple

from fastapi import HTTPException, status

from app.models.auth.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import MemberRole, WorkspaceMember
from app.repositories import workspace_members as members_repository
from app.repositories import workspaces as workspaces_repository


class WorkspaceAccess(NamedTuple):
    workspace: Workspace
    membership: WorkspaceMember


def require_user_id(current_user: User) -> str:
    if not current_user.id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user is missing an id",
        )
    return current_user.id


async def get_workspace_membership(workspace_id: str, current_user: User) -> WorkspaceAccess:
    workspace = await workspaces_repository.find_active_by_id(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    membership = await members_repository.find_by_workspace_and_user(
        workspace.id,
        require_user_id(current_user),
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this workspace",
        )
    return WorkspaceAccess(workspace=workspace, membership=membership)


async def require_workspace_member(workspace_id: str, current_user: User) -> WorkspaceAccess:
    return await get_workspace_membership(workspace_id, current_user)


async def require_workspace_admin(
    workspace_id: str,
    current_user: User,
    *,
    detail: str = "You do not have permission to perform this action",
) -> WorkspaceAccess:
    access = await get_workspace_membership(workspace_id, current_user)
    if access.membership.role not in (MemberRole.OWNER, MemberRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return access


async def require_workspace_owner(
    workspace_id: str,
    current_user: User,
    *,
    detail: str = "You do not have permission to perform this action",
) -> WorkspaceAccess:
    access = await get_workspace_membership(workspace_id, current_user)
    if access.membership.role != MemberRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return access
