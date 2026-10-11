import hashlib
import secrets
from datetime import timedelta

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.database import transaction
from app.core import events
from app.core.time import as_utc, utc_now
from app.models.auth.user import User
from app.models.workspace_member import MemberRole
from app.repositories import invitations as invitations_repository
from app.repositories import workspace_members as members_repository
from app.repositories import workspaces as workspaces_repository
from app.schemas.invitation import InvitationCreated
from app.schemas.workspace import WorkspaceResponse, workspace_response
from app.services.activity import record_member_event
from app.services.workspace_access import require_user_id, require_workspace_admin

_MAX_TOKEN_LENGTH = 512


def hash_invite_token(token: str) -> str:
    # Random invite tokens are high entropy, so SHA-256 is enough to look them up
    # without storing the raw value.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _invite_url(token: str) -> str:
    base = settings.app_base_url.rstrip("/")
    return f"{base}/invite/{token}"


async def create_invitation(workspace_id: str, current_user: User) -> InvitationCreated:
    access = await require_workspace_admin(
        workspace_id,
        current_user,
        detail="Only the workspace owner or an admin can invite members",
    )
    token = secrets.token_urlsafe(32)
    now = utc_now()
    await invitations_repository.insert(
        {
            "workspace_id": access.workspace.id,
            "token_hash": hash_invite_token(token),
            "created_by": access.membership.user_id,
            "expires_at": now + timedelta(days=settings.invitation_ttl_days),
            "used_at": None,
            "created_at": now,
            "revoked_at": None,
        }
    )
    return InvitationCreated(invite_url=_invite_url(token))


async def join_invitation(invite_token: str, current_user: User) -> WorkspaceResponse:
    user_id = require_user_id(current_user)
    if not invite_token or len(invite_token) > _MAX_TOKEN_LENGTH:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")

    invitation = await invitations_repository.find_by_token_hash(hash_invite_token(invite_token))
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if as_utc(invitation.expires_at) <= utc_now():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation has expired")
    if invitation.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation has been revoked")

    workspace = await workspaces_repository.find_by_id(invitation.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if workspace.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This workspace is no longer available",
        )

    existing = await members_repository.find_by_workspace_and_user(workspace.id, user_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this workspace",
        )

    now = utc_now()
    try:
        async with transaction() as session:
            await members_repository.insert(
                {
                    "workspace_id": workspace.id,
                    "user_id": user_id,
                    "role": MemberRole.MEMBER.value,
                    "joined_at": now,
                    "updated_at": now,
                },
                session=session,
            )
            await invitations_repository.mark_used(invitation.id, now, session=session)
            event = await record_member_event(
                workspace.id,
                user_id,
                "member.joined",
                session=session,
            )
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this workspace",
        ) from None

    events.publish(event)
    return workspace_response(workspace)
