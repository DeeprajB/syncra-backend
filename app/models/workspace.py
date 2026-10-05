from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Workspace(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    title: str
    description: Optional[str] = None
    owner_id: str
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None
