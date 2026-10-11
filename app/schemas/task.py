from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.time import as_utc
from app.models.task import Task, TaskPriority, TaskStatus


def _strip_required_text(value: Optional[str], message: str) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError(message)
    return stripped


def _strip_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _strip_optional_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("Value cannot be blank")
    return stripped


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return as_utc(value)


def _dates_in_order(start_date: Optional[datetime], due_date: Optional[datetime]) -> None:
    if start_date is not None and due_date is not None and as_utc(start_date) > as_utc(due_date):
        raise ValueError("Start date cannot be after the due date")


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee_id: Optional[str] = None
    start_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    parent_task_id: Optional[str] = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return _strip_required_text(value, "Title cannot be blank")

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: Optional[str]) -> Optional[str]:
        return _strip_optional_text(value)

    @field_validator("assignee_id", "parent_task_id")
    @classmethod
    def strip_id(cls, value: Optional[str]) -> Optional[str]:
        return _strip_optional_id(value)

    @field_validator("start_date", "due_date")
    @classmethod
    def normalize_dates(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_dates(self) -> "TaskCreate":
        _dates_in_order(self.start_date, self.due_date)
        return self


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    assignee_id: Optional[str] = None
    start_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    estimated_hours: Optional[float] = Field(default=None, ge=0)
    parent_task_id: Optional[str] = None
    version: Optional[int] = Field(default=None, ge=1)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: Optional[str]) -> Optional[str]:
        return _strip_required_text(value, "Title cannot be blank")

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: Optional[str]) -> Optional[str]:
        return _strip_optional_text(value)

    @field_validator("assignee_id", "parent_task_id")
    @classmethod
    def strip_id(cls, value: Optional[str]) -> Optional[str]:
        return _strip_optional_id(value)

    @field_validator("start_date", "due_date")
    @classmethod
    def normalize_dates(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_update(self) -> "TaskUpdate":
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Title cannot be null")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("Status cannot be null")
        if "priority" in self.model_fields_set and self.priority is None:
            raise ValueError("Priority cannot be null")
        if "start_date" in self.model_fields_set and "due_date" in self.model_fields_set:
            _dates_in_order(self.start_date, self.due_date)
        return self


class TaskMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatus
    position: int = Field(ge=0)
    version: Optional[int] = Field(default=None, ge=1)


class TaskReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatus
    task_ids: List[str] = Field(min_length=0)


class TaskResponse(BaseModel):
    id: str
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
    version: int


class TaskListResponse(BaseModel):
    items: List[TaskResponse]
    next_cursor: Optional[str] = None


class TaskReorderResponse(BaseModel):
    items: List[TaskResponse]


def task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status,
        priority=task.priority,
        assignee_id=task.assignee_id,
        created_by=task.created_by,
        start_date=task.start_date,
        due_date=task.due_date,
        position=task.position,
        parent_task_id=task.parent_task_id,
        estimated_hours=task.estimated_hours,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
        version=task.version,
    )


def plain_value(value: object) -> object:
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
