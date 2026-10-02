from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class User(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    sub: str
    email: str = ""
    given_name: str = ""
    family_name: str = ""
    picture: str = ""
    email_verified: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
