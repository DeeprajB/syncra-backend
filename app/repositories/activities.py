from typing import Any, Dict, List

from app.core.database import database
from app.core.time import as_utc
from app.models.activity import Activity


def serialize_activity(document: Dict[str, Any]) -> Activity:
    return Activity(
        id=str(document["_id"]),
        workspace_id=document["workspace_id"],
        project_id=document.get("project_id"),
        task_id=document.get("task_id"),
        actor_id=document["actor_id"],
        action=document["action"],
        metadata=document.get("metadata") or {},
        created_at=document["created_at"],
    )


async def ensure_indexes() -> None:
    await database.activities.create_index(
        [("task_id", 1), ("created_at", -1)],
        name="task_activity",
    )
    await database.activities.create_index(
        [("project_id", 1), ("created_at", -1)],
        name="project_activity",
    )
    await database.activities.create_index(
        [("workspace_id", 1), ("created_at", -1), ("_id", -1)],
        name="workspace_activity",
    )


async def list_for_workspace(workspace_id: str) -> List[Activity]:
    documents = await database.activities.find({"workspace_id": workspace_id}).to_list()
    activities = [serialize_activity(document) for document in documents]
    activities.sort(
        key=lambda activity: (as_utc(activity.created_at), activity.id),
        reverse=True,
    )
    return activities


async def list_for_task(task_id: str, limit: int = 50) -> List[Activity]:
    cursor = (
        database.activities.find({"task_id": task_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    documents = await cursor.to_list()
    return [serialize_activity(document) for document in documents]


async def insert(document: Dict[str, Any], session: Any = None) -> Activity:
    payload = dict(document)
    payload["metadata"] = dict(payload.get("metadata") or {})
    result = await database.activities.insert_one(payload, session=session)
    payload["_id"] = result.inserted_id
    return serialize_activity(payload)
