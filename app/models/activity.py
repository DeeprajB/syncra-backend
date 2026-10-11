from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class Activity(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    workspace_id: str
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    actor_id: str
    action: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
