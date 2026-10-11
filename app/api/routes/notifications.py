from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from app.models.auth.user import User
from app.schemas.notification import (
    NotificationItem,
    NotificationListResponse,
    UnreadCountResponse,
)
from app.services import notification as notification_service
from app.services.auth import sync_current_user

router = APIRouter(tags=["Notifications"])


@router.get(
    "/api/v1/notifications",
    response_model=NotificationListResponse,
    summary="List your notifications",
    description="Returns the signed-in user's notifications, newest first.",
)
async def list_notifications(
    current_user: User = Depends(sync_current_user),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: Optional[str] = None,
) -> NotificationListResponse:
    return await notification_service.list_notifications(
        current_user,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/api/v1/notifications/unread-count",
    response_model=UnreadCountResponse,
    summary="Count unread notifications",
)
async def unread_count(
    current_user: User = Depends(sync_current_user),
) -> UnreadCountResponse:
    return await notification_service.unread_count(current_user)


@router.post(
    "/api/v1/notifications/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark all notifications read",
)
async def mark_all_read(current_user: User = Depends(sync_current_user)) -> None:
    await notification_service.mark_all_read(current_user)


@router.patch(
    "/api/v1/notifications/{notification_id}/read",
    response_model=NotificationItem,
    summary="Mark one notification read",
)
async def mark_read(
    notification_id: str,
    current_user: User = Depends(sync_current_user),
) -> NotificationItem:
    return await notification_service.mark_read(notification_id, current_user)
