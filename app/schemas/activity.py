from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.models.activity import Activity


class ActivityItem(BaseModel):
    id: str
    task_id: Optional[str] = None
    actor_id: str
    action: str
    metadata: Dict[str, Any]
    created_at: datetime


class ActivityListResponse(BaseModel):
    items: List[ActivityItem]


class WorkspaceActivityItem(ActivityItem):
    project_id: Optional[str] = None
    actor_name: str = "Unknown"
    avatar_url: str = ""


class WorkspaceActivityListResponse(BaseModel):
    items: List[WorkspaceActivityItem]
    next_cursor: Optional[str] = None


def activity_item(activity: Activity) -> ActivityItem:
    return ActivityItem(
        id=activity.id,
        task_id=activity.task_id,
        actor_id=activity.actor_id,
        action=activity.action,
        metadata=activity.metadata,
        created_at=activity.created_at,
    )
