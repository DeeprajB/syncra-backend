from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.database import database
from app.core.mongo import parse_object_id
from app.core.time import as_utc, utc_now
from app.models.task import STATUS_RANK, Task, TaskPriority, TaskStatus


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, str):
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    return value


def serialize_task(document: Dict[str, Any]) -> Task:
    estimated_hours = document.get("estimated_hours")
    return Task(
        id=str(document["_id"]),
        project_id=document["project_id"],
        title=document["title"],
        description=document.get("description"),
        status=TaskStatus(document["status"]),
        priority=TaskPriority(document["priority"]),
        assignee_id=document.get("assignee_id"),
        created_by=document["created_by"],
        start_date=_parse_datetime(document.get("start_date")),
        due_date=_parse_datetime(document.get("due_date")),
        position=int(document["position"]),
        parent_task_id=document.get("parent_task_id"),
        estimated_hours=None if estimated_hours is None else float(estimated_hours),
        created_at=_parse_datetime(document["created_at"]),
        updated_at=_parse_datetime(document["updated_at"]),
        completed_at=_parse_datetime(document.get("completed_at")),
        version=int(document.get("version", 1)),
    )


async def ensure_indexes() -> None:
    await database.tasks.create_index(
        [("project_id", 1), ("status", 1), ("position", 1)],
        name="project_status_position",
    )
    await database.tasks.create_index(
        [("project_id", 1), ("updated_at", -1)],
        name="project_updated",
    )
    await database.tasks.create_index(
        [("assignee_id", 1), ("status", 1), ("due_date", 1)],
        name="assignee_status_due",
    )
    await database.tasks.create_index("parent_task_id", name="parent_task")


async def insert(document: Dict[str, Any], session: Any = None) -> Task:
    payload = dict(document)
    result = await database.tasks.insert_one(payload, session=session)
    payload["_id"] = result.inserted_id
    return serialize_task(payload)


async def find_by_id(task_id: str, session: Any = None) -> Optional[Task]:
    object_id = parse_object_id(task_id)
    if object_id is None:
        return None
    document = await database.tasks.find_one({"_id": object_id}, session=session)
    if document is None:
        return None
    return serialize_task(document)


async def list_for_project(
    project_id: str,
    *,
    status: Optional[str] = None,
    assignee_id: Optional[str] = None,
    priority: Optional[str] = None,
    session: Any = None,
) -> List[Task]:
    query: Dict[str, Any] = {"project_id": project_id}
    if status is not None:
        query["status"] = status
    if assignee_id is not None:
        query["assignee_id"] = assignee_id
    if priority is not None:
        query["priority"] = priority
    cursor = database.tasks.find(query, session=session)
    documents = await cursor.to_list()
    tasks = [serialize_task(document) for document in documents]
    tasks.sort(key=lambda task: (STATUS_RANK[task.status], task.position, task.id))
    return tasks


async def list_by_parent(parent_task_id: str, session: Any = None) -> List[Task]:
    cursor = database.tasks.find({"parent_task_id": parent_task_id}, session=session)
    documents = await cursor.to_list()
    return [serialize_task(document) for document in documents]


async def update(
    task_id: str,
    fields: Dict[str, Any],
    *,
    expected_version: Optional[int] = None,
    session: Any = None,
) -> Optional[Task]:
    object_id = parse_object_id(task_id)
    if object_id is None:
        return None
    query: Dict[str, Any] = {"_id": object_id}
    if expected_version is not None:
        query["version"] = expected_version
    payload = dict(fields)
    payload["updated_at"] = utc_now()
    result = await database.tasks.update_one(
        query,
        {"$set": payload, "$inc": {"version": 1}},
        session=session,
    )
    if result.matched_count == 0:
        return None
    return await find_by_id(task_id, session=session)


async def delete(task_id: str, session: Any = None) -> bool:
    object_id = parse_object_id(task_id)
    if object_id is None:
        return False
    result = await database.tasks.delete_one({"_id": object_id}, session=session)
    return result.deleted_count == 1
