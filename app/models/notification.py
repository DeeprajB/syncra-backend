from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Notification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
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
    deduplication_key: str
