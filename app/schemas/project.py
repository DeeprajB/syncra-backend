from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.project import Project, ProjectStatus


def _strip_title(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("Title cannot be blank")
    return stripped


def _strip_description(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _dates_in_order(start_date: Optional[date], due_date: Optional[date]) -> None:
    if start_date is not None and due_date is not None and due_date < start_date:
        raise ValueError("Due date cannot be before the start date")


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    status: ProjectStatus = ProjectStatus.PLANNING
    start_date: Optional[date] = None
    due_date: Optional[date] = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return _strip_title(value)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: Optional[str]) -> Optional[str]:
        return _strip_description(value)

    @model_validator(mode="after")
    def validate_dates(self) -> "ProjectCreate":
        _dates_in_order(self.start_date, self.due_date)
        return self


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[ProjectStatus] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: Optional[str]) -> Optional[str]:
        return _strip_title(value)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: Optional[str]) -> Optional[str]:
        return _strip_description(value)

    @model_validator(mode="after")
    def validate_update(self) -> "ProjectUpdate":
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Title cannot be null")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("Status cannot be null")
        if "start_date" in self.model_fields_set and "due_date" in self.model_fields_set:
            _dates_in_order(self.start_date, self.due_date)
        return self


class ProjectResponse(BaseModel):
    id: str
    workspace_id: str
    title: str
    description: Optional[str] = None
    status: ProjectStatus
    owner_id: str
    created_by: str
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime


def project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        workspace_id=project.workspace_id,
        title=project.title,
        description=project.description,
        status=project.status,
        owner_id=project.owner_id,
        created_by=project.created_by,
        start_date=project.start_date,
        due_date=project.due_date,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )
