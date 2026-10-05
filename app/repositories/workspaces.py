from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.database import database
from app.core.mongo import parse_object_id
from app.core.time import utc_now
from app.models.workspace import Workspace

_EDITABLE_FIELDS = {"title", "description"}


def serialize_workspace(document: Dict[str, Any]) -> Workspace:
    return Workspace(
        id=str(document["_id"]),
        title=document["title"],
        description=document.get("description"),
        owner_id=document["owner_id"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        archived_at=document.get("archived_at"),
    )


async def ensure_indexes() -> None:
    await database.workspaces.create_index("owner_id")


async def insert(document: Dict[str, Any], session: Any = None) -> Workspace:
    payload = dict(document)
    result = await database.workspaces.insert_one(payload, session=session)
    payload["_id"] = result.inserted_id
    return serialize_workspace(payload)


async def find_by_id(workspace_id: str) -> Optional[Workspace]:
    object_id = parse_object_id(workspace_id)
    if object_id is None:
        return None
    document = await database.workspaces.find_one({"_id": object_id})
    if document is None:
        return None
    return serialize_workspace(document)


async def find_active_by_id(workspace_id: str) -> Optional[Workspace]:
    object_id = parse_object_id(workspace_id)
    if object_id is None:
        return None
    document = await database.workspaces.find_one({"_id": object_id, "archived_at": None})
    if document is None:
        return None
    return serialize_workspace(document)


async def find_active_by_ids(workspace_ids: List[str]) -> List[Workspace]:
    object_ids = []
    for workspace_id in workspace_ids:
        object_id = parse_object_id(workspace_id)
        if object_id is not None:
            object_ids.append(object_id)
    if not object_ids:
        return []
    cursor = database.workspaces.find({"_id": {"$in": object_ids}, "archived_at": None})
    documents = await cursor.to_list()
    return [serialize_workspace(document) for document in documents]


async def update(workspace_id: str, fields: Dict[str, Any]) -> Optional[Workspace]:
    object_id = parse_object_id(workspace_id)
    if object_id is None:
        return None
    updates = {key: value for key, value in fields.items() if key in _EDITABLE_FIELDS}
    updates["updated_at"] = utc_now()
    result = await database.workspaces.update_one(
        {"_id": object_id, "archived_at": None},
        {"$set": updates},
    )
    if result.matched_count == 0:
        return None
    return await find_active_by_id(workspace_id)


async def archive(workspace_id: str, archived_at: datetime) -> bool:
    object_id = parse_object_id(workspace_id)
    if object_id is None:
        return False
    result = await database.workspaces.update_one(
        {"_id": object_id, "archived_at": None},
        {"$set": {"archived_at": archived_at, "updated_at": archived_at}},
    )
    return result.matched_count == 1
