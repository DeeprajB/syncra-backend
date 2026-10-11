from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from app.models.notification import Notification


class NotificationItem(BaseModel):
    id: str
    recipient_id: str
    workspace_id: str
    project_id: str
    task_id: str
    actor_id: str
    type: str
    entity_type: str
    entity_id: str
    title: str
    body: str
    created_at: datetime
    read_at: Optional[datetime] = None


class NotificationListResponse(BaseModel):
    items: List[NotificationItem]
    next_cursor: Optional[str] = None


class UnreadCountResponse(BaseModel):
    count: int


def notification_item(notification: Notification) -> NotificationItem:
    return NotificationItem(
        id=notification.id,
        recipient_id=notification.recipient_id,
        workspace_id=notification.workspace_id,
        project_id=notification.project_id,
        task_id=notification.task_id,
        actor_id=notification.actor_id,
        type=notification.type,
        entity_type=notification.entity_type,
        entity_id=notification.entity_id,
        title=notification.title,
        body=notification.body,
        created_at=notification.created_at,
        read_at=notification.read_at,
    )
