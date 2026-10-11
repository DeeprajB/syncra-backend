from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


STATUS_RANK = {
    TaskStatus.TODO: 0,
    TaskStatus.IN_PROGRESS: 1,
    TaskStatus.REVIEW: 2,
    TaskStatus.DONE: 3,
}


class ActivityAction(str, Enum):
    CREATED = "task.created"
    UPDATED = "task.updated"
    ASSIGNED = "task.assigned"
    STATUS_CHANGED = "task.status_changed"
    MOVED = "task.moved"
    DELETED = "task.deleted"


class Task(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    project_id: str
    title: str
    description: Optional[str] = None
    status: TaskStatus
    priority: TaskPriority
    assignee_id: Optional[str] = None
    created_by: str
    start_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    position: int
    parent_task_id: Optional[str] = None
    estimated_hours: Optional[float] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    version: int = 1
