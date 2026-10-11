import logging
from typing import Dict, List

from fastapi import HTTPException, status

from app.core import events
from app.core.database import transaction
from app.core.time import utc_now
from app.models.auth.user import User
from app.models.workspace_member import MemberRole, WorkspaceMember
from app.repositories import users as users_repository
from app.repositories import workspace_members as members_repository
from app.repositories import workspaces as workspaces_repository
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceUpdate,
    workspace_response,
)
from app.schemas.workspace_member import (
    WorkspaceMemberResponse,
    WorkspaceMemberUpdate,
    workspace_member_response,
)
from app.services.activity import record_member_event
from app.services.workspace_access import (
    require_user_id,
    require_workspace_admin,
    require_workspace_member,
    require_workspace_owner,
)

logger = logging.getLogger(__name__)

_ROLE_ORDER = {
    MemberRole.OWNER: 0,
    MemberRole.ADMIN: 1,
    MemberRole.MEMBER: 2,
    MemberRole.VIEWER: 3,
}


async def create_workspace(current_user: User, payload: WorkspaceCreate) -> WorkspaceResponse:
    user_id = require_user_id(current_user)
    now = utc_now()
    async with transaction() as session:
        workspace = await workspaces_repository.insert(
            {
                "title": payload.title,
                "description": payload.description,
                "owner_id": user_id,
                "created_at": now,
                "updated_at": now,
                "archived_at": None,
            },
            session=session,
        )
        await members_repository.insert(
            {
                "workspace_id": workspace.id,
                "user_id": user_id,
                "role": MemberRole.OWNER.value,
                "joined_at": now,
                "updated_at": now,
            },
            session=session,
        )
    return workspace_response(workspace)


async def list_workspaces(current_user: User) -> List[WorkspaceResponse]:
    memberships = await members_repository.list_by_user(require_user_id(current_user))
    workspaces = await workspaces_repository.find_active_by_ids(
        [membership.workspace_id for membership in memberships]
    )
    workspaces.sort(key=lambda workspace: workspace.updated_at, reverse=True)
    return [workspace_response(workspace) for workspace in workspaces]


async def get_workspace(workspace_id: str, current_user: User) -> WorkspaceResponse:
    access = await require_workspace_member(workspace_id, current_user)
    return workspace_response(access.workspace)


async def update_workspace(
    workspace_id: str,
    current_user: User,
    payload: WorkspaceUpdate,
) -> WorkspaceResponse:
    access = await require_workspace_admin(
        workspace_id,
        current_user,
        detail="Only the workspace owner or an admin can update this workspace",
    )
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    updated = await workspaces_repository.update(access.workspace.id, changes)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace_response(updated)


async def delete_workspace(workspace_id: str, current_user: User) -> None:
    access = await require_workspace_owner(
        workspace_id,
        current_user,
        detail="Only the workspace owner can delete this workspace",
    )
    archived = await workspaces_repository.archive(access.workspace.id, utc_now())
    if not archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")


async def list_members(workspace_id: str, current_user: User) -> List[WorkspaceMemberResponse]:
    access = await require_workspace_member(workspace_id, current_user)
    members = await members_repository.list_by_workspace(access.workspace.id)
    members.sort(key=lambda member: (_ROLE_ORDER[member.role], member.joined_at))
    users = await users_repository.find_by_ids([member.user_id for member in members])
    users_by_id: Dict[str, User] = {user.id: user for user in users if user.id}
    return [
        workspace_member_response(member, users_by_id.get(member.user_id))
        for member in members
    ]


async def update_member_role(
    workspace_id: str,
    member_id: str,
    current_user: User,
    payload: WorkspaceMemberUpdate,
) -> WorkspaceMemberResponse:
    access = await require_workspace_owner(
        workspace_id,
        current_user,
        detail="Only the workspace owner can change member roles",
    )
    member = await members_repository.find_in_workspace(access.workspace.id, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if member.role == MemberRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The owner role cannot be changed without transferring ownership",
        )

    new_role = MemberRole(payload.role)
    if new_role != member.role:
        updated = await members_repository.update_role(
            access.workspace.id,
            member.id,
            new_role.value,
            utc_now(),
        )
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
        member = updated
    return await _member_response(member)


async def remove_member(workspace_id: str, member_id: str, current_user: User) -> None:
    access = await require_workspace_member(workspace_id, current_user)
    member = await members_repository.find_in_workspace(access.workspace.id, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    _assert_can_remove(access.membership, member)
    deleted = await members_repository.delete(access.workspace.id, member.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    await _member_departed(access.workspace.id, member.user_id)


async def leave_workspace(workspace_id: str, current_user: User) -> None:
    access = await require_workspace_member(workspace_id, current_user)
    if access.membership.role == MemberRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The workspace owner cannot leave. Delete the workspace or transfer ownership first.",
        )
    deleted = await members_repository.delete(access.workspace.id, access.membership.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    await _member_departed(access.workspace.id, access.membership.user_id)


def _assert_can_remove(actor: WorkspaceMember, target: WorkspaceMember) -> None:
    if target.id == actor.id:
        if actor.role == MemberRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The workspace owner cannot be removed",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Leave the workspace to remove yourself",
        )
    if target.role == MemberRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The workspace owner cannot be removed",
        )
    if actor.role == MemberRole.OWNER:
        return
    if actor.role == MemberRole.ADMIN and target.role in (MemberRole.MEMBER, MemberRole.VIEWER):
        return
    if actor.role == MemberRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins can only remove members and viewers",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to remove this member",
    )


async def _member_departed(workspace_id: str, user_id: str) -> None:
    try:
        event = await record_member_event(workspace_id, user_id, "member.left")
        events.publish(event)
    except Exception:
        logger.exception("Failed to record member.left")
    try:
        from app.realtime.manager import manager

        await manager.disconnect_user(workspace_id, user_id)
    except Exception:
        logger.exception("Failed to disconnect removed member")


async def _member_response(member: WorkspaceMember) -> WorkspaceMemberResponse:
    user = await users_repository.find_by_id(member.user_id)
    return workspace_member_response(member, user)
