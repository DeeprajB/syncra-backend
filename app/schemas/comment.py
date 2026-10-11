from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Comment cannot be empty")
        return text


class CommentResponse(BaseModel):
    id: str
    task_id: str
    author_id: str
    author_name: str
    avatar_url: str
    content: str
    created_at: datetime
    updated_at: datetime


class CommentListResponse(BaseModel):
    items: List[CommentResponse]
    next_cursor: Optional[str] = None
