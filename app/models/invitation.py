from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Invitation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    workspace_id: str
    token_hash: str
    created_by: str
    expires_at: datetime
    used_at: Optional[datetime] = None
    created_at: datetime
    revoked_at: Optional[datetime] = None
