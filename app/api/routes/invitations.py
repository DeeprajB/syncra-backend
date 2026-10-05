from fastapi import APIRouter, Depends

from app.models.auth.user import User
from app.schemas.workspace import WorkspaceResponse
from app.services import invitation as invitation_service
from app.services.auth import sync_current_user

router = APIRouter(prefix="/api/v1/invitations", tags=["Invitations"])


@router.post(
    "/{invite_token}/join",
    response_model=WorkspaceResponse,
    summary="Join a workspace",
    description="Accept an invite token and join that workspace as a member.",
)
async def join_invitation(
    invite_token: str,
    current_user: User = Depends(sync_current_user),
) -> WorkspaceResponse:
    return await invitation_service.join_invitation(invite_token, current_user)
