from typing import Any, Dict

from app.core.database import database
from app.models.activity import Activity


def serialize_activity(document: Dict[str, Any]) -> Activity:
    return Activity(
        id=str(document["_id"]),
        workspace_id=document["workspace_id"],
        project_id=document["project_id"],
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


async def insert(document: Dict[str, Any], session: Any = None) -> Activity:
    payload = dict(document)
    payload["metadata"] = dict(payload.get("metadata") or {})
    result = await database.activities.insert_one(payload, session=session)
    payload["_id"] = result.inserted_id
    return serialize_activity(payload)
