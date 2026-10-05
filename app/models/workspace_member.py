from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MemberRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class WorkspaceMember(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    workspace_id: str
    user_id: str
    role: MemberRole
    joined_at: datetime
    updated_at: datetime
