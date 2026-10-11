from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.models.auth.user import User
from app.schemas.activity import WorkspaceActivityListResponse
from app.services import activity as activity_service
from app.services.auth import sync_current_user

router = APIRouter(tags=["Activity"])


@router.get(
    "/api/v1/workspaces/{workspace_id}/activity",
    response_model=WorkspaceActivityListResponse,
    summary="List workspace activity",
    description=(
        "Returns the newest workspace activity first. "
        "Any member, including a viewer, can read it. "
        "Group items by day in the client."
    ),
)
async def list_workspace_activity(
    workspace_id: str,
    current_user: User = Depends(sync_current_user),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = None,
) -> WorkspaceActivityListResponse:
    return await activity_service.list_workspace_activity(
        workspace_id,
        current_user,
        limit=limit,
        cursor=cursor,
    )
