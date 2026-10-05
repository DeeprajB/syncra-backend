from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.models.auth.user import User
from app.models.workspace_member import MemberRole, WorkspaceMember


class WorkspaceMemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "member", "viewer"]


class WorkspaceMemberResponse(BaseModel):
    id: str
    user_id: str
    name: str
    email: str
    avatar_url: str
    role: MemberRole
    joined_at: datetime


def display_name(user: User) -> str:
    parts = [user.given_name.strip(), user.family_name.strip()]
    name = " ".join(part for part in parts if part)
    if name:
        return name
    if user.email:
        return user.email
    return "Unknown"


def workspace_member_response(
    member: WorkspaceMember,
    user: Optional[User],
) -> WorkspaceMemberResponse:
    if user is None:
        return WorkspaceMemberResponse(
            id=member.id,
            user_id=member.user_id,
            name="Unknown",
            email="",
            avatar_url="",
            role=member.role,
            joined_at=member.joined_at,
        )

    return WorkspaceMemberResponse(
        id=member.id,
        user_id=member.user_id,
        name=display_name(user),
        email=user.email,
        avatar_url=user.picture or "",
        role=member.role,
        joined_at=member.joined_at,
    )
