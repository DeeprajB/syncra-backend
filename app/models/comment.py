from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Comment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    workspace_id: str
    project_id: str
    task_id: str
    author_id: str
    content: str
    created_at: datetime
    updated_at: datetime
