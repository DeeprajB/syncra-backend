from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.core.database import database
from app.core.mongo import parse_object_id
from app.core.time import utc_now
from app.models.project import Project, ProjectStatus

_EDITABLE_FIELDS = {"title", "description", "status", "start_date", "due_date"}
_DATE_FIELDS = {"start_date", "due_date"}


def _stored_date(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _prepare_value(key: str, value: Any) -> Any:
    if key in _DATE_FIELDS:
        return _stored_date(value)
    if isinstance(value, ProjectStatus):
        return value.value
    return value


def serialize_project(document: Dict[str, Any]) -> Project:
    return Project(
        id=str(document["_id"]),
        workspace_id=document["workspace_id"],
        title=document["title"],
        description=document.get("description"),
        status=ProjectStatus(document["status"]),
        owner_id=document["owner_id"],
        created_by=document["created_by"],
        start_date=_parse_date(document.get("start_date")),
        due_date=_parse_date(document.get("due_date")),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        archived_at=document.get("archived_at"),
    )


async def ensure_indexes() -> None:
    await database.projects.create_index(
        [("workspace_id", 1), ("archived_at", 1), ("updated_at", -1)],
        name="workspace_archived_updated",
    )


async def insert(document: Dict[str, Any], session: Any = None) -> Project:
    payload = {key: _prepare_value(key, value) for key, value in document.items()}
    result = await database.projects.insert_one(payload, session=session)
    payload["_id"] = result.inserted_id
    return serialize_project(payload)


async def find_active_by_id(project_id: str) -> Optional[Project]:
    object_id = parse_object_id(project_id)
    if object_id is None:
        return None
    document = await database.projects.find_one({"_id": object_id, "archived_at": None})
    if document is None:
        return None
    return serialize_project(document)


async def list_active_by_workspace(workspace_id: str) -> List[Project]:
    cursor = database.projects.find({"workspace_id": workspace_id, "archived_at": None})
    documents = await cursor.to_list()
    projects = [serialize_project(document) for document in documents]
    projects.sort(key=lambda project: project.updated_at, reverse=True)
    return projects


async def update(project_id: str, fields: Dict[str, Any]) -> Optional[Project]:
    object_id = parse_object_id(project_id)
    if object_id is None:
        return None
    updates = {
        key: _prepare_value(key, value)
        for key, value in fields.items()
        if key in _EDITABLE_FIELDS
    }
    updates["updated_at"] = utc_now()
    result = await database.projects.update_one(
        {"_id": object_id, "archived_at": None},
        {"$set": updates},
    )
    if result.matched_count == 0:
        return None
    return await find_active_by_id(project_id)


async def archive(project_id: str, archived_at: datetime) -> bool:
    object_id = parse_object_id(project_id)
    if object_id is None:
        return False
    result = await database.projects.update_one(
        {"_id": object_id, "archived_at": None},
        {"$set": {"archived_at": archived_at, "updated_at": archived_at}},
    )
    return result.matched_count == 1
