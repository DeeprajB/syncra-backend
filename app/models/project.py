from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProjectStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Project(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
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
    archived_at: Optional[datetime] = None
