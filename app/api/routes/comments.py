from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from app.models.auth.user import User
from app.schemas.comment import CommentBody, CommentListResponse, CommentResponse
from app.services import comment as comment_service
from app.services.auth import sync_current_user

router = APIRouter(tags=["Comments"])


@router.post(
    "/api/v1/tasks/{task_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Comment on a task",
    description="Owners, admins, and members can comment. Viewers can read comments but cannot create them.",
)
async def create_comment(
    task_id: str,
    payload: CommentBody,
    current_user: User = Depends(sync_current_user),
) -> CommentResponse:
    return await comment_service.create_comment(task_id, current_user, payload)


@router.get(
    "/api/v1/tasks/{task_id}/comments",
    response_model=CommentListResponse,
    summary="List task comments",
    description="Returns comments oldest first. Any workspace member, including a viewer, can read them.",
)
async def list_comments(
    task_id: str,
    current_user: User = Depends(sync_current_user),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = None,
) -> CommentListResponse:
    return await comment_service.list_comments(task_id, current_user, limit=limit, cursor=cursor)


@router.patch(
    "/api/v1/comments/{comment_id}",
    response_model=CommentResponse,
    summary="Edit a comment",
    description="Only the comment author can edit it. Viewers cannot.",
)
async def update_comment(
    comment_id: str,
    payload: CommentBody,
    current_user: User = Depends(sync_current_user),
) -> CommentResponse:
    return await comment_service.update_comment(comment_id, current_user, payload)


@router.delete(
    "/api/v1/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a comment",
    description="The author can delete their comment. Owners and admins can delete any comment in the workspace.",
)
async def delete_comment(
    comment_id: str,
    current_user: User = Depends(sync_current_user),
) -> None:
    await comment_service.delete_comment(comment_id, current_user)
