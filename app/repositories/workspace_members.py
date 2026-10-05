from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.database import database
from app.core.mongo import parse_object_id
from app.models.workspace_member import MemberRole, WorkspaceMember


def serialize_member(document: Dict[str, Any]) -> WorkspaceMember:
    return WorkspaceMember(
        id=str(document["_id"]),
        workspace_id=document["workspace_id"],
        user_id=document["user_id"],
        role=MemberRole(document["role"]),
        joined_at=document["joined_at"],
        updated_at=document["updated_at"],
    )


async def ensure_indexes() -> None:
    await database.workspace_members.create_index(
        [("workspace_id", 1), ("user_id", 1)],
        unique=True,
        name="workspace_user_unique",
    )
    await database.workspace_members.create_index("user_id")


async def insert(document: Dict[str, Any], session: Any = None) -> WorkspaceMember:
    payload = dict(document)
    result = await database.workspace_members.insert_one(payload, session=session)
    payload["_id"] = result.inserted_id
    return serialize_member(payload)


async def find_by_workspace_and_user(
    workspace_id: str,
    user_id: str,
) -> Optional[WorkspaceMember]:
    document = await database.workspace_members.find_one(
        {"workspace_id": workspace_id, "user_id": user_id}
    )
    if document is None:
        return None
    return serialize_member(document)


async def find_in_workspace(workspace_id: str, member_id: str) -> Optional[WorkspaceMember]:
    object_id = parse_object_id(member_id)
    if object_id is None:
        return None
    document = await database.workspace_members.find_one(
        {"_id": object_id, "workspace_id": workspace_id}
    )
    if document is None:
        return None
    return serialize_member(document)


async def list_by_user(user_id: str) -> List[WorkspaceMember]:
    cursor = database.workspace_members.find({"user_id": user_id})
    documents = await cursor.to_list()
    return [serialize_member(document) for document in documents]


async def list_by_workspace(workspace_id: str) -> List[WorkspaceMember]:
    cursor = database.workspace_members.find({"workspace_id": workspace_id})
    documents = await cursor.to_list()
    return [serialize_member(document) for document in documents]


async def update_role(
    workspace_id: str,
    member_id: str,
    role: str,
    updated_at: datetime,
) -> Optional[WorkspaceMember]:
    object_id = parse_object_id(member_id)
    if object_id is None:
        return None
    result = await database.workspace_members.update_one(
        {"_id": object_id, "workspace_id": workspace_id},
        {"$set": {"role": role, "updated_at": updated_at}},
    )
    if result.matched_count == 0:
        return None
    return await find_in_workspace(workspace_id, member_id)


async def delete(workspace_id: str, member_id: str) -> bool:
    object_id = parse_object_id(member_id)
    if object_id is None:
        return False
    result = await database.workspace_members.delete_one(
        {"_id": object_id, "workspace_id": workspace_id}
    )
    return result.deleted_count == 1
